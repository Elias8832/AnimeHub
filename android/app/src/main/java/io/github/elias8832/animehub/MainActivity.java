package io.github.elias8832.animehub;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.content.pm.PackageInfo;
import android.content.res.AssetManager;
import android.os.Bundle;
import android.util.Log;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

/** The whole app: start the hub (Python) and show it. Decision 88. */
public class MainActivity extends Activity {
    static final String TAG = "AnimeHub";
    static String hub;  // http://127.0.0.1:<the app's own port>/
    /** This start's secret: the hub answers only requests that carry it (a cookie only this WebView has). */
    static final String KEY = newKey();

    static String newKey() {
        byte[] b = new byte[32];
        new java.security.SecureRandom().nextBytes(b);
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x));
        return s.toString();
    }
    WebView web;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        TermuxBridge.ctx = getApplicationContext();
        NetCheck.ctx = getApplicationContext();
        Updater.ctx = getApplicationContext();
        web = new WebView(this);
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setMediaPlaybackRequiresUserGesture(false);
        web.setWebViewClient(new Links());
        web.addJavascriptInterface(new Page(), "AnimeHubApp");
        web.setBackgroundColor(0xff101114);
        if ((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0)
            WebView.setWebContentsDebuggingEnabled(true);  // test builds only: chrome://inspect
        setContentView(web);
        if (state != null) web.restoreState(state);
        else web.loadData("<body style='background:#101114'></body>", "text/html", "utf-8");
        if (TermuxBridge.termuxInstalled() && !TermuxBridge.allowed())
            requestPermissions(new String[]{TermuxBridge.PERMISSION}, 1);
        new Thread(this::startHub, "hub-start").start();
    }

    /** Termux allowed: read its ani-cli now and reload, so the page sees online episodes at once. */
    @Override public void onRequestPermissionsResult(int code, String[] perms, int[] results) {
        if (results.length == 0 || results[0] != android.content.pm.PackageManager.PERMISSION_GRANTED) return;
        if (code == 2) { web.evaluateJavascript("window.hubFilesBack && hubFilesBack()", null); return; }  // Android 8–10
        new Thread(() -> {
            try {
                while (hub == null || !up()) Thread.sleep(100);
                Python.getInstance().getModule("animehub.android").callAttr("fetch_anicli", true);
            } catch (Exception e) {
                Log.e(TAG, "ani-cli check after the permission failed", e);
            }
            runOnUiThread(() -> web.reload());
        }, "termux-allowed").start();
    }

    /**
     * Where a link goes. The hub stays here; so do AniList's and MyAnimeList's
     * sign-in pages. Their way back is registered for the PC's address
     * (localhost:8787), which this app brings to its own hub. Anything else
     * (F-Droid, a show's AniList page) opens in the phone's browser.
     */
    class Links extends WebViewClient {
        /** A sign-in is under way: AniList or MyAnimeList pages stay here until it comes back. */
        boolean signingIn;

        @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
            Uri u = req.getUrl();
            String host = u.getHost() == null ? "" : u.getHost();
            if (hub != null && u.toString().startsWith(hub)) {
                signingIn = false;
                return false;
            }
            if ((host.equals("localhost") || host.equals("127.0.0.1")) && u.getPort() == 8787
                    && u.getPath() != null && u.getPath().startsWith("/auth/") && hub != null) {
                String back = hub + u.getPath().substring(1)
                        + (u.getEncodedQuery() != null ? "?" + u.getEncodedQuery() : "")
                        + (u.getEncodedFragment() != null ? "#" + u.getEncodedFragment() : "");
                signingIn = false;
                v.loadUrl(back);
                return true;
            }
            // AniList's and MyAnimeList's sign-in stays here until it comes back,
            // including the login page their sign-in address redirects to at once.
            String path = u.getPath() == null ? "" : u.getPath();
            boolean account = host.endsWith("anilist.co") || host.endsWith("myanimelist.net");
            if (account && (path.startsWith("/api/v2/oauth") || path.startsWith("/v1/oauth2"))) signingIn = true;
            if (account && signingIn) return false;
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, u));
            } catch (Exception e) {
                Log.e(TAG, "no app opens " + u, e);
            }
            return true;
        }
    }

    /** Back from Android's file-access screen: reload, so the page sees the answer. */
    boolean askedFiles;

    @Override protected void onResume() {
        super.onResume();
        if (askedFiles) {
            askedFiles = false;
            web.evaluateJavascript("window.hubFilesBack && hubFilesBack()", null);  // keeps the tour's page
        }
    }

    /** What the page may ask of the app: window.AnimeHubApp. */
    class Page {
        /** Your own anime files: Android's "All files access" screen (11+), or the plain read prompt. */
        @JavascriptInterface public void askFiles() {
            runOnUiThread(() -> {
                if (android.os.Build.VERSION.SDK_INT >= 30) {
                    askedFiles = true;
                    try {
                        startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                                Uri.parse("package:" + getPackageName())));
                    } catch (Exception e) {
                        startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
                    }
                } else {
                    requestPermissions(new String[]{"android.permission.READ_EXTERNAL_STORAGE"}, 2);
                }
            });
        }

        /** Ask for the Termux permission when it is missing (Settings or the tour's "Check again"). */
        @JavascriptInterface public void askTermux() {
            runOnUiThread(() -> {
                if (TermuxBridge.termuxInstalled() && !TermuxBridge.allowed())
                    requestPermissions(new String[]{TermuxBridge.PERMISSION}, 1);
            });
        }
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override public void onBackPressed() {
        if (web.canGoBack()) web.goBack();  // the page keeps its own history (pushNav)
        else super.onBackPressed();
    }

    void startHub() {
        try {
            synchronized (MainActivity.class) {
                if (hub == null) {
                    File code = unpack();
                    if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
                    int port = Python.getInstance().getModule("animehub_start")
                            .callAttr("run", code.getPath(), getFilesDir().getPath(), KEY).toInt();
                    hub = "http://127.0.0.1:" + port + "/";
                    android.webkit.CookieManager cm = android.webkit.CookieManager.getInstance();
                    cm.setCookie(hub, "animehub_app=" + KEY + "; Path=/; HttpOnly; SameSite=Strict");
                    cm.flush();
                }
            }
            for (int i = 0; i < 400; i++) {
                if (up()) {
                    runOnUiThread(() -> { String u = web.getUrl(); if (u == null || !u.startsWith(hub)) web.loadUrl(hub); });
                    return;
                }
                Thread.sleep(100);
            }
            Log.e(TAG, "the hub did not start within 40 s");
        } catch (Exception e) {
            Log.e(TAG, "the hub could not start", e);
        }
    }

    static boolean up() {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(hub).openConnection();
            c.setConnectTimeout(300);
            c.setRequestProperty("Cookie", "animehub_app=" + KEY);
            return c.getResponseCode() == 200;
        } catch (IOException e) {
            return false;
        }
    }

    /** The hub's code out of the APK, once per installed version. */
    File unpack() throws Exception {
        File hub = new File(getFilesDir(), "hub"), mark = new File(getFilesDir(), "hub.version");
        PackageInfo p = getPackageManager().getPackageInfo(getPackageName(), 0);
        String want = p.versionName + " " + p.lastUpdateTime;
        if (hub.isDirectory() && mark.isFile()
                && want.equals(new String(Files.readAllBytes(mark.toPath()), StandardCharsets.UTF_8))) return hub;
        delete(hub);
        copy(getAssets(), "hub", hub);
        Files.write(mark.toPath(), want.getBytes(StandardCharsets.UTF_8));
        return hub;
    }

    static void delete(File f) {
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) delete(k);
        f.delete();
    }

    static void copy(AssetManager am, String path, File dst) throws IOException {
        String[] kids = am.list(path);
        if (kids != null && kids.length > 0) {
            dst.mkdirs();
            for (String k : kids) copy(am, path + "/" + k, new File(dst, k));
            return;
        }
        try (InputStream in = am.open(path); OutputStream out = new FileOutputStream(dst)) {
            byte[] buf = new byte[65536];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        }
    }
}
