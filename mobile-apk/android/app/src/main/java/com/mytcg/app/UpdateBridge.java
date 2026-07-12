package com.mytcg.app;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.webkit.JavascriptInterface;

/**
 * Exposes the installed app's version and an external-browser opener to the
 * webapp, so js/update.js can offer an in-app "update available" prompt.
 *
 * Gameplay stays fully offline; this bridge only backs the optional update
 * check, which fails silently when there is no network.
 */
public class UpdateBridge {
    private final Activity activity;

    UpdateBridge(Activity activity) {
        this.activity = activity;
    }

    /** The installed build's versionCode (see app/build.gradle). 0 on failure. */
    @JavascriptInterface
    public int versionCode() {
        try {
            PackageInfo info = activity.getPackageManager()
                    .getPackageInfo(activity.getPackageName(), 0);
            return info.versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    /**
     * Open a URL in the system browser so its download manager fetches the new
     * APK (the user then installs it through the normal package installer). We
     * deliberately do not install directly, which would need extra permissions.
     */
    @JavascriptInterface
    public void openUrl(String url) {
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity.startActivity(intent);
        } catch (Exception e) {
            // No browser able to handle the link; nothing sensible to do.
        }
    }
}
