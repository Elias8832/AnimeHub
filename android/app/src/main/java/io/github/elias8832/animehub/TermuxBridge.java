package io.github.elias8832.animehub;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Runs one bash script in the user's Termux (RUN_COMMAND) and keeps the answer
 * for the hub (animehub/android.py) to collect. Decision 88.
 */
public class TermuxBridge {
    static final String PERMISSION = "com.termux.permission.RUN_COMMAND";
    static Context ctx;
    static final AtomicInteger next = new AtomicInteger(1);
    static final ConcurrentHashMap<Integer, String[]> results = new ConcurrentHashMap<>();

    static boolean termuxInstalled() {
        try {
            ctx.getPackageManager().getPackageInfo("com.termux", 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    static boolean allowed() {
        return ctx.checkSelfPermission(PERMISSION) == PackageManager.PERMISSION_GRANTED;
    }

    /** A request id; the answer arrives in result(id). */
    public static int run(String script) {
        int id = next.incrementAndGet();
        if (!termuxInstalled()) { fail(id, "no-termux"); return id; }
        if (!allowed()) { fail(id, "no-permission"); return id; }
        try {
            Intent back = new Intent(ctx, TermuxResult.class).putExtra("id", id);
            PendingIntent pi = PendingIntent.getService(ctx, id, back,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE);
            Intent i = new Intent("com.termux.RUN_COMMAND");
            i.setClassName("com.termux", "com.termux.app.RunCommandService");
            i.putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/usr/bin/bash");
            i.putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-c", script});
            i.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
            i.putExtra("com.termux.RUN_COMMAND_PENDING_INTENT", pi);
            // As Termux's docs say: a foreground start, or Android refuses it
            // whenever Termux isn't already open ("Background start not allowed").
            if (android.os.Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i);
            else ctx.startService(i);
        } catch (Exception e) {
            fail(id, String.valueOf(e));
        }
        return id;
    }

    /** {stdout, stderr, exit code, Termux's error text} once answered, else null. */
    public static String[] result(int id) {
        return results.remove(id);
    }

    static void fail(int id, String why) {
        results.put(id, new String[]{"", "", "-1", why});
    }

    static void answer(int id, Bundle r) {
        if (r == null) { fail(id, "Termux sent no result"); return; }
        String msg = r.getString("errmsg", "");
        results.put(id, new String[]{r.getString("stdout", ""), r.getString("stderr", ""),
                String.valueOf(r.getInt("exitCode", -1)), msg == null ? "" : msg});
    }
}
