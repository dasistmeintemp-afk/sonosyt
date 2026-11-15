package io.github.sonosyt;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.drawable.Icon;
import android.media.MediaMetadata;
import android.media.VolumeProvider;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;

/**
 * Keeps the Sonos room controllable while the app is in the background or the
 * phone is locked: a media notification with play/pause/skip, and a media
 * session through which the phone's volume keys set the Sonos volume.
 *
 * It only mirrors the sonosyt server: state comes from GET /api/status,
 * commands go to POST /api/control.
 */
public class PlaybackService extends Service {
    static final String ACTION_REFRESH = "io.github.sonosyt.REFRESH";
    static final String ACTION_PLAY = "io.github.sonosyt.PLAY";
    static final String ACTION_PAUSE = "io.github.sonosyt.PAUSE";
    static final String ACTION_NEXT = "io.github.sonosyt.NEXT";
    static final String ACTION_PREVIOUS = "io.github.sonosyt.PREVIOUS";

    static final String PREFS = "sonosyt";
    static final String PREF_SERVER = "server_url";
    static final String PREF_SPEAKER = "speaker";

    private static final String CHANNEL = "playback";
    private static final int NOTIFICATION_ID = 1;
    private static final int VOLUME_STEP = 2;
    private static final long POLL_PLAYING_MS = 3000;
    private static final long POLL_IDLE_MS = 10000;
    /** Nothing playing for this long: the notification goes away. */
    private static final long IDLE_STOP_MS = 15 * 60 * 1000;
    /** Server unreachable this many polls in a row (left the Wi-Fi): stop. */
    private static final int MAX_FAILURES = 12;

    private SharedPreferences prefs;
    private MediaSession session;
    private VolumeProvider volumeProvider;
    private HandlerThread thread;
    private Handler worker;
    private Handler main;
    private PowerManager.WakeLock wakeLock;
    private boolean remoteVolume;

    // Last known state of the room. Written on the worker thread.
    private volatile String speaker = "";
    private volatile String title = "";
    private volatile String artist = "";
    private volatile boolean playing;
    private volatile boolean live;
    private volatile long positionMs;
    private volatile long durationMs;
    private volatile Bitmap art;
    private String artUrl = "";
    private String shownKey = "";
    private long lastActive;
    private long volumeTouched;
    private int failures;
    private volatile int wantedVolume = -1;

    @Override
    public void onCreate() {
        super.onCreate();
        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        main = new Handler(Looper.getMainLooper());
        thread = new HandlerThread("sonosyt-playback");
        thread.start();
        worker = new Handler(thread.getLooper());
        wakeLock = ((PowerManager) getSystemService(POWER_SERVICE))
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "sonosyt:command");
        wakeLock.setReferenceCounted(false);

        NotificationChannel channel = new NotificationChannel(
                CHANNEL, "Wiedergabe", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("Steuerung der Sonos-Wiedergabe");
        channel.setShowBadge(false);
        getSystemService(NotificationManager.class).createNotificationChannel(channel);

        session = new MediaSession(this, "sonosyt");
        session.setCallback(new MediaSession.Callback() {
            @Override
            public void onPlay() {
                command("play", null);
            }

            @Override
            public void onPause() {
                command("pause", null);
            }

            @Override
            public void onStop() {
                command("pause", null);
            }

            @Override
            public void onSkipToNext() {
                command("next", null);
            }

            @Override
            public void onSkipToPrevious() {
                command("previous", null);
            }

            @Override
            public void onSeekTo(long pos) {
                command("seek", (int) (pos / 1000));
            }
        });
        volumeProvider = new VolumeProvider(VolumeProvider.VOLUME_CONTROL_ABSOLUTE, 100, 0) {
            @Override
            public void onAdjustVolume(int direction) {
                if (direction == 1 || direction == -1) {
                    changeVolume(getCurrentVolume() + direction * VOLUME_STEP);
                }
            }

            @Override
            public void onSetVolumeTo(int volume) {
                changeVolume(volume);
            }
        };
        lastActive = SystemClock.elapsedRealtime();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        speaker = prefs.getString(PREF_SPEAKER, "");
        startForeground(NOTIFICATION_ID, buildNotification(),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        String action = intent == null ? ACTION_REFRESH : intent.getAction();
        if (ACTION_PLAY.equals(action)) {
            command("play", null);
        } else if (ACTION_PAUSE.equals(action)) {
            command("pause", null);
        } else if (ACTION_NEXT.equals(action)) {
            command("next", null);
        } else if (ACTION_PREVIOUS.equals(action)) {
            command("previous", null);
        } else {
            lastActive = SystemClock.elapsedRealtime();
            failures = 0;
            pollNow();
        }
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        worker.removeCallbacksAndMessages(null);
        thread.quitSafely();
        session.setActive(false);
        session.release();
        super.onDestroy();
    }

    // -- polling ---------------------------------------------------------------

    private final Runnable poll = new Runnable() {
        @Override
        public void run() {
            boolean keepGoing = refresh();
            if (keepGoing) {
                worker.postDelayed(this, playing ? POLL_PLAYING_MS : POLL_IDLE_MS);
            } else {
                main.post(new Runnable() {
                    @Override
                    public void run() {
                        stopForeground(true);
                        stopSelf();
                    }
                });
            }
        }
    };

    private void pollNow() {
        worker.removeCallbacks(poll);
        worker.post(poll);
    }

    /** Fetches the room's state and shows it. False when the service should end. */
    private boolean refresh() {
        String server = prefs.getString(PREF_SERVER, null);
        String room = prefs.getString(PREF_SPEAKER, null);
        if (server == null || room == null) {
            return false;
        }
        speaker = room;
        JSONObject status = null;
        try {
            status = request(server + "/api/status?speaker=" + URLEncoder.encode(room, "UTF-8"),
                    null);
        } catch (Exception ignored) {
        }
        long now = SystemClock.elapsedRealtime();
        if (status == null) {
            failures++;
            return failures < MAX_FAILURES;
        }
        failures = 0;

        String state = status.optString("state");
        title = status.optString("title");
        artist = status.optString("artist");
        playing = "PLAYING".equals(state) || "TRANSITIONING".equals(state);
        live = status.optBoolean("live");
        positionMs = status.optLong("position") * 1000;
        durationMs = status.optLong("duration") * 1000;
        loadArt(status.optString("album_art"));

        if (playing) {
            lastActive = now;
        } else if (now - lastActive > IDLE_STOP_MS) {
            return false;
        }

        int volume = status.optInt("volume", -1);
        if (volume >= 0 && now - volumeTouched > 3000) {
            volumeProvider.setCurrentVolume(volume);
        }
        if (!remoteVolume && volume >= 0) {
            // From here on the volume keys belong to the Sonos room while it plays.
            session.setPlaybackToRemote(volumeProvider);
            session.setActive(true);
            remoteVolume = true;
        }
        show();
        return true;
    }

    private void loadArt(String url) {
        if (url.equals(artUrl)) {
            return;
        }
        artUrl = url;
        art = null;
        if (url.isEmpty()) {
            return;
        }
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(6000);
            if (conn.getResponseCode() == 200) {
                InputStream in = conn.getInputStream();
                art = BitmapFactory.decodeStream(in);
                in.close();
            }
        } catch (Exception ignored) {
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    // -- showing state -----------------------------------------------------------

    private void show() {
        boolean hasTrack = !title.isEmpty();
        boolean seekable = hasTrack && !live && durationMs > 0;
        long actions = PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE
                | PlaybackState.ACTION_PLAY_PAUSE | PlaybackState.ACTION_SKIP_TO_NEXT
                | PlaybackState.ACTION_SKIP_TO_PREVIOUS
                | (seekable ? PlaybackState.ACTION_SEEK_TO : 0);
        int state = playing ? PlaybackState.STATE_PLAYING
                : hasTrack ? PlaybackState.STATE_PAUSED : PlaybackState.STATE_STOPPED;
        session.setPlaybackState(new PlaybackState.Builder()
                .setActions(actions)
                .setState(state, positionMs, playing ? 1f : 0f, SystemClock.elapsedRealtime())
                .build());

        // Metadata and notification only when something visible changed.
        String key = title + "|" + artist + "|" + playing + "|" + seekable + "|" + durationMs
                + "|" + speaker + "|" + (art != null) + "|" + artUrl;
        if (key.equals(shownKey)) {
            return;
        }
        shownKey = key;
        MediaMetadata.Builder meta = new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE,
                        hasTrack ? title : "Nichts in Wiedergabe")
                .putString(MediaMetadata.METADATA_KEY_ARTIST,
                        artist.isEmpty() ? speaker : artist + " · " + speaker)
                .putLong(MediaMetadata.METADATA_KEY_DURATION, seekable ? durationMs : -1);
        if (art != null) {
            meta.putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, art);
        }
        session.setMetadata(meta.build());
        getSystemService(NotificationManager.class).notify(NOTIFICATION_ID, buildNotification());
    }

    private Notification buildNotification() {
        boolean hasTrack = !title.isEmpty();
        int immutable = PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT;
        Notification.Builder builder = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(hasTrack ? title : "sonosyt")
                .setContentText(hasTrack
                        ? (artist.isEmpty() ? speaker : artist + " · " + speaker)
                        : (speaker.isEmpty() ? "Verbunden" : "Verbunden mit " + speaker))
                .setContentIntent(PendingIntent.getActivity(
                        this, 0, new Intent(this, MainActivity.class), immutable))
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(playing)
                .setShowWhen(false)
                .setOnlyAlertOnce(true)
                .addAction(action(R.drawable.ic_prev, "Zurück", ACTION_PREVIOUS, 1))
                .addAction(playing
                        ? action(R.drawable.ic_pause, "Pause", ACTION_PAUSE, 2)
                        : action(R.drawable.ic_play, "Wiedergabe", ACTION_PLAY, 3))
                .addAction(action(R.drawable.ic_next, "Weiter", ACTION_NEXT, 4))
                .setStyle(new Notification.MediaStyle()
                        .setMediaSession(session.getSessionToken())
                        .setShowActionsInCompactView(0, 1, 2));
        if (art != null) {
            builder.setLargeIcon(art);
        }
        return builder.build();
    }

    private Notification.Action action(int icon, String label, String action, int requestCode) {
        PendingIntent intent = PendingIntent.getService(this, requestCode,
                new Intent(this, PlaybackService.class).setAction(action),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Action.Builder(Icon.createWithResource(this, icon), label, intent)
                .build();
    }

    // -- commands ------------------------------------------------------------------

    private void command(final String action, final Integer value) {
        wakeLock.acquire(8000); // the phone may be asleep: stay up until it is sent
        lastActive = SystemClock.elapsedRealtime();
        worker.post(new Runnable() {
            @Override
            public void run() {
                try {
                    JSONObject body = new JSONObject();
                    body.put("speaker", prefs.getString(PREF_SPEAKER, ""));
                    body.put("action", action);
                    if (value != null) {
                        body.put("value", value.intValue());
                    }
                    request(prefs.getString(PREF_SERVER, "") + "/api/control", body);
                } catch (Exception ignored) {
                }
                worker.removeCallbacks(poll);
                worker.postDelayed(poll, 500);
            }
        });
    }

    private final Runnable sendVolume = new Runnable() {
        @Override
        public void run() {
            int volume = wantedVolume;
            try {
                JSONObject body = new JSONObject();
                body.put("speaker", prefs.getString(PREF_SPEAKER, ""));
                body.put("volume", volume);
                request(prefs.getString(PREF_SERVER, "") + "/api/control", body);
            } catch (Exception ignored) {
            }
        }
    };

    private void changeVolume(int volume) {
        volume = Math.max(0, Math.min(100, volume));
        volumeProvider.setCurrentVolume(volume);
        volumeTouched = SystemClock.elapsedRealtime();
        wantedVolume = volume;
        wakeLock.acquire(8000);
        // Presses in quick succession: only the latest value needs to be sent.
        worker.removeCallbacks(sendVolume);
        worker.post(sendVolume);
    }

    /** GET (body == null) or POST JSON; the parsed answer, or null on any failure. */
    private static JSONObject request(String url, JSONObject body) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(8000);
            if (body != null) {
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                OutputStream out = conn.getOutputStream();
                out.write(body.toString().getBytes("UTF-8"));
                out.close();
            }
            if (conn.getResponseCode() != 200) {
                return null;
            }
            InputStream in = conn.getInputStream();
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int n;
            while ((n = in.read(chunk)) > 0) {
                buffer.write(chunk, 0, n);
            }
            in.close();
            return new JSONObject(buffer.toString("UTF-8"));
        } catch (Exception e) {
            return null;
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }
}
