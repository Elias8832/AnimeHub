package io.github.elias8832.animehub;

import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.os.IBinder;
import android.util.Log;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * Updates (Decision 88): hands the APK the hub downloaded and checked to
 * Android's installer. Android then asks "Do you want to update this app?";
 * nothing installs without that tap.
 */
public class Updater {
    static Context ctx;

    public static void install(String path) throws Exception {
        File apk = new File(path);
        PackageInstaller pi = ctx.getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        params.setAppPackageName(ctx.getPackageName());
        int id = pi.createSession(params);
        try (PackageInstaller.Session s = pi.openSession(id)) {
            try (InputStream in = new FileInputStream(apk); OutputStream out = s.openWrite("AnimeHub.apk", 0, apk.length())) {
                byte[] buf = new byte[1 << 16];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                s.fsync(out);
            }
            Intent back = new Intent(ctx, Result.class);
            PendingIntent p = PendingIntent.getService(ctx, id, back,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE);
            s.commit(p.getIntentSender());
        }
    }

    /** Android's answer: show its "Update this app?" screen when it needs the user. */
    public static class Result extends Service {
        @Override public int onStartCommand(Intent intent, int flags, int startId) {
            int status = intent == null ? -99 : intent.getIntExtra(PackageInstaller.EXTRA_STATUS, -99);
            if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                Intent ask = intent.getParcelableExtra(Intent.EXTRA_INTENT);
                if (ask != null) {
                    ask.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(ask);
                }
            } else if (status != PackageInstaller.STATUS_SUCCESS) {
                Log.e("AnimeHub", "update not installed: " + (intent == null ? "" : intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)));
            }
            stopSelf(startId);
            return START_NOT_STICKY;
        }

        @Override public IBinder onBind(Intent i) { return null; }
    }
}
