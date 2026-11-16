package io.github.sonosyt;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;

/**
 * Target of "Teilen" in the YouTube app. Shows nothing: it hands the link to the
 * playback service and closes at once, so you stay in YouTube.
 *
 * Reached through two aliases in the manifest: ".SharePlay" plays now,
 * ".ShareQueue" adds to the end of the queue.
 */
public class ShareActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Intent intent = getIntent();
        String text = intent == null ? null : intent.getStringExtra(Intent.EXTRA_TEXT);
        String link = MainActivity.firstUrl(text);
        SharedPreferences prefs = getSharedPreferences(PlaybackService.PREFS, MODE_PRIVATE);
        boolean ready = prefs.getString(PlaybackService.PREF_SERVER, null) != null
                && prefs.getString(PlaybackService.PREF_SPEAKER, null) != null;
        if (link == null || !ready) {
            // Not set up yet (or no link): show the app, which asks for the server first.
            Intent open = new Intent(this, MainActivity.class);
            if (link != null) {
                open.setAction(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, link);
            }
            startActivity(open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } else {
            ComponentName target = intent.getComponent();
            boolean queue = target != null && target.getClassName().endsWith("ShareQueue");
            startForegroundService(new Intent(this, PlaybackService.class)
                    .setAction(PlaybackService.ACTION_SHARE)
                    .putExtra(PlaybackService.EXTRA_LINK, link)
                    .putExtra(PlaybackService.EXTRA_QUEUE, queue));
        }
        finish();
        overridePendingTransition(0, 0);
    }
}
