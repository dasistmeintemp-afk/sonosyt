package io.github.sonosyt;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.KeyEvent;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.NetworkInterface;
import java.net.Socket;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Remote control for a sonosyt server in the home network: shows the server's
 * web UI and accepts links shared from the YouTube app.
 */
public class MainActivity extends Activity {
    private static final int DEFAULT_PORT = 8327;
    private static final String PREF_SERVER = PlaybackService.PREF_SERVER;
    private static final String NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS";
    /** Link in the web UI (gear icon) that opens the server setup screen. */
    private static final String SETTINGS_URL = "sonosyt://settings";
    private static final Pattern URL_IN_TEXT = Pattern.compile("https?://\\S+");

    private SharedPreferences prefs;
    private WebView web;
    private View setup;
    private EditText address;
    private TextView status;
    private Button connect;
    private Button scan;
    /** Link shared before a server was configured; opened once one is. */
    private String pendingQuery;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PlaybackService.PREFS, MODE_PRIVATE);

        FrameLayout root = new FrameLayout(this);
        web = buildWebView();
        setup = buildSetupView();
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        root.addView(setup, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);

        if (savedInstanceState == null) {
            pendingQuery = sharedLink(getIntent());
        }
        // The media notification needs this on Android 13+. Asked once.
        if (Build.VERSION.SDK_INT >= 33 && !prefs.getBoolean("asked_notifications", false)
                && checkSelfPermission(NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            prefs.edit().putBoolean("asked_notifications", true).apply();
            requestPermissions(new String[] {NOTIFICATIONS}, 1);
        }
        if (server() == null) {
            showSetup(null);
        } else {
            openServer();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        startPlaybackService();
    }

    /** Starts (or wakes) the background controls once server and room are known. */
    private void startPlaybackService() {
        if (server() == null || prefs.getString(PlaybackService.PREF_SPEAKER, null) == null) {
            return;
        }
        try {
            startForegroundService(new Intent(this, PlaybackService.class)
                    .setAction(PlaybackService.ACTION_REFRESH));
        } catch (Exception ignored) { // not allowed right now, e.g. app not in front
        }
    }

    /** Called by the web UI (window.SonosytApp). */
    private final class Bridge {
        @JavascriptInterface
        public void setSpeaker(final String name) {
            if (name == null || name.isEmpty() || name.length() > 200) {
                return;
            }
            prefs.edit().putString(PlaybackService.PREF_SPEAKER, name).apply();
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    startPlaybackService();
                }
            });
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        String link = sharedLink(intent);
        if (link != null) {
            pendingQuery = link;
            if (server() != null) {
                openServer();
            }
        }
    }

    // -- web view ----------------------------------------------------------

    private WebView buildWebView() {
        WebView view = new WebView(this);
        view.getSettings().setJavaScriptEnabled(true);
        view.getSettings().setDomStorageEnabled(true); // remembers the chosen speaker
        // Tells the web UI it runs inside the app (settings button, update hint).
        String version = "0";
        try {
            version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception ignored) {
        }
        view.getSettings().setUserAgentString(
                view.getSettings().getUserAgentString() + " sonosyt-app/" + version);
        view.addJavascriptInterface(new Bridge(), "SonosytApp");
        view.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, String url) {
                if (url.startsWith(SETTINGS_URL)) {
                    showSetup(null);
                    return true;
                }
                String base = server();
                if (base != null && url.startsWith(base)) {
                    return false;
                }
                try { // anything else belongs in the browser
                    startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                } catch (Exception ignored) {
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView v, int code, String description, String url) {
                showSetup("Server nicht erreichbar (" + description + "). "
                        + "Läuft sonosyt und ist das Handy im Heim-WLAN?");
            }
        });
        return view;
    }

    private void openServer() {
        String url = server() + "/";
        if (pendingQuery != null) {
            url += "?q=" + Uri.encode(pendingQuery);
            pendingQuery = null;
        }
        setup.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        web.loadUrl(url);
    }

    // -- setup screen ------------------------------------------------------

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private View buildSetupView() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(24), dp(32), dp(24), dp(24));

        TextView title = new TextView(this);
        title.setText("Mit sonosyt verbinden");
        title.setTextSize(22);
        box.addView(title);

        TextView hint = new TextView(this);
        hint.setText("Adresse des sonosyt-Servers im Heimnetz (z. B. dein NAS). "
                + "Das Handy muss im selben WLAN sein.");
        hint.setPadding(0, dp(8), 0, dp(16));
        box.addView(hint);

        address = new EditText(this);
        address.setHint("192.168.1.10:" + DEFAULT_PORT);
        address.setSingleLine(true);
        address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        address.setImeOptions(EditorInfo.IME_ACTION_GO);
        address.setOnEditorActionListener(new TextView.OnEditorActionListener() {
            @Override
            public boolean onEditorAction(TextView v, int actionId, KeyEvent event) {
                tryConnect();
                return true;
            }
        });
        box.addView(address);

        connect = new Button(this);
        connect.setText("Verbinden");
        connect.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                tryConnect();
            }
        });
        box.addView(connect);

        scan = new Button(this);
        scan.setText("Im Netzwerk suchen");
        scan.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                scanNetwork();
            }
        });
        box.addView(scan);

        status = new TextView(this);
        status.setPadding(0, dp(16), 0, 0);
        status.setGravity(Gravity.START);
        box.addView(status);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(box);
        return scroll;
    }

    private void showSetup(String message) {
        web.setVisibility(View.GONE);
        setup.setVisibility(View.VISIBLE);
        String current = server();
        if (current != null && address.getText().length() == 0) {
            address.setText(current);
        }
        status.setText(message == null ? "" : message);
        setBusy(false);
    }

    private void setBusy(boolean busy) {
        connect.setEnabled(!busy);
        scan.setEnabled(!busy);
    }

    private void tryConnect() {
        final String base = normalize(address.getText().toString());
        if (base == null) {
            status.setText("Bitte eine Adresse eingeben, z. B. 192.168.1.10");
            return;
        }
        setBusy(true);
        status.setText("Verbinde mit " + base + " …");
        new Thread(new Runnable() {
            @Override
            public void run() {
                final boolean ok = isSonosyt(base, 4000);
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (ok) {
                            useServer(base);
                        } else {
                            setBusy(false);
                            status.setText("Unter " + base + " antwortet kein sonosyt-Server.");
                        }
                    }
                });
            }
        }).start();
    }

    private void useServer(String base) {
        prefs.edit().putString(PREF_SERVER, base).apply();
        address.setText(base);
        openServer();
    }

    /** Looks for a sonosyt server on every address of the phone's /24 networks. */
    private void scanNetwork() {
        setBusy(true);
        status.setText("Suche im Netzwerk …");
        new Thread(new Runnable() {
            @Override
            public void run() {
                final String found = scanBlocking();
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (found != null) {
                            useServer(found);
                        } else {
                            setBusy(false);
                            status.setText("Kein sonosyt-Server gefunden. Ist das Handy im "
                                    + "Heim-WLAN? Sonst die Adresse bitte von Hand eingeben.");
                        }
                    }
                });
            }
        }).start();
    }

    private String scanBlocking() {
        List<String> prefixes = new ArrayList<String>();
        try {
            for (NetworkInterface nic : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!nic.isUp() || nic.isLoopback()) {
                    continue;
                }
                for (InetAddress addr : Collections.list(nic.getInetAddresses())) {
                    if (addr instanceof Inet4Address && addr.isSiteLocalAddress()) {
                        String ip = addr.getHostAddress();
                        String prefix = ip.substring(0, ip.lastIndexOf('.') + 1);
                        if (!prefixes.contains(prefix)) {
                            prefixes.add(prefix);
                        }
                    }
                }
            }
        } catch (Exception ignored) {
        }
        final AtomicReference<String> found = new AtomicReference<String>();
        ExecutorService pool = Executors.newFixedThreadPool(64);
        for (String prefix : prefixes) {
            for (int host = 1; host < 255; host++) {
                final String ip = prefix + host;
                pool.execute(new Runnable() {
                    @Override
                    public void run() {
                        if (found.get() != null || !portOpen(ip, DEFAULT_PORT, 600)) {
                            return;
                        }
                        String base = "http://" + ip + ":" + DEFAULT_PORT;
                        if (isSonosyt(base, 2000)) {
                            found.compareAndSet(null, base);
                        }
                    }
                });
            }
        }
        pool.shutdown();
        try {
            pool.awaitTermination(30, TimeUnit.SECONDS);
        } catch (InterruptedException ignored) {
        }
        pool.shutdownNow();
        return found.get();
    }

    // -- helpers -----------------------------------------------------------

    private String server() {
        return prefs.getString(PREF_SERVER, null);
    }

    /** "192.168.1.10" -> "http://192.168.1.10:8327"; null if unusable. */
    static String normalize(String input) {
        String text = input.trim();
        while (text.endsWith("/")) {
            text = text.substring(0, text.length() - 1);
        }
        if (text.isEmpty()) {
            return null;
        }
        if (!text.matches("(?i)^https?://.*")) {
            text = "http://" + text;
        }
        Matcher m = Pattern.compile("(?i)^(https?)://([^/:\\s]+|\\[[0-9a-f:]+\\])(:\\d{1,5})?$")
                .matcher(text);
        if (!m.matches()) {
            return null;
        }
        if (m.group(3) == null && m.group(1).equalsIgnoreCase("http")) {
            text += ":" + DEFAULT_PORT;
        }
        return text;
    }

    private static boolean portOpen(String ip, int port, int timeoutMs) {
        Socket socket = new Socket();
        try {
            socket.connect(new InetSocketAddress(ip, port), timeoutMs);
            return true;
        } catch (Exception e) {
            return false;
        } finally {
            try {
                socket.close();
            } catch (Exception ignored) {
            }
        }
    }

    /** True if a sonosyt server answers GET /api/ping at this base URL. */
    private static boolean isSonosyt(String base, int timeoutMs) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(base + "/api/ping").openConnection();
            conn.setConnectTimeout(timeoutMs);
            conn.setReadTimeout(timeoutMs);
            if (conn.getResponseCode() != 200) {
                return false;
            }
            InputStream in = conn.getInputStream();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[512];
            int n;
            while ((n = in.read(buf)) > 0 && out.size() < 4096) {
                out.write(buf, 0, n);
            }
            return out.toString("UTF-8").contains("\"sonosyt\"");
        } catch (Exception e) {
            return false;
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    /** The first URL in text shared to the app, e.g. from YouTube's share sheet. */
    private static String sharedLink(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) {
            return null;
        }
        String text = intent.getStringExtra(Intent.EXTRA_TEXT);
        if (text == null) {
            return null;
        }
        Matcher m = URL_IN_TEXT.matcher(text);
        return m.find() ? m.group() : null;
    }

    // -- keys / navigation ---------------------------------------------------

    /** While the remote is showing, the phone's volume keys set the Sonos volume. */
    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        int code = event.getKeyCode();
        boolean volumeKey = code == KeyEvent.KEYCODE_VOLUME_UP
                || code == KeyEvent.KEYCODE_VOLUME_DOWN;
        if (volumeKey && server() != null && web.getVisibility() == View.VISIBLE) {
            if (event.getAction() == KeyEvent.ACTION_DOWN) { // repeats while held
                int direction = code == KeyEvent.KEYCODE_VOLUME_UP ? 1 : -1;
                web.loadUrl("javascript:void(window.sonosytVolumeKey&&sonosytVolumeKey("
                        + direction + "))");
            }
            return true; // also swallow ACTION_UP so the phone's own volume stays put
        }
        return super.dispatchKeyEvent(event);
    }

    @Override
    public void onBackPressed() {
        if (setup.getVisibility() == View.VISIBLE && server() != null
                && web.getUrl() != null) {
            setup.setVisibility(View.GONE);
            web.setVisibility(View.VISIBLE);
        } else if (web.getVisibility() == View.VISIBLE && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
