package io.github.elias8832.animehub;

import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * Sync with the PC over the home network itself (Decision 91). A VPN on the
 * phone carries everything, the home network too, and its server can't reach
 * the PC. Android lets an app send over Wi-Fi past the VPN when the VPN app
 * allows that; the sync only talks to the PC at home, so nothing leaves the
 * home network this way. When the VPN doesn't allow it, the call fails and
 * the hub tries the normal way.
 */
public class Lan {
    /** The Wi-Fi or Ethernet network under any VPN, or null. */
    static Network home() {
        ConnectivityManager cm = NetCheck.ctx.getSystemService(ConnectivityManager.class);
        for (Network n : cm.getAllNetworks()) {
            NetworkCapabilities caps = cm.getNetworkCapabilities(n);
            if (caps == null || caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue;
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                    || caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) return n;
        }
        return null;
    }

    /** Whether a VPN is up that the home network sits under. */
    public static boolean behindVpn() {
        return !NetCheck.vpn().isEmpty();
    }

    /**
     * One request to the PC over the home network: {status, body, Set-Cookie
     * lines joined by "\n"}. body null: a GET. Throws when there is no home
     * network or the VPN won't let the app past it.
     */
    public static String[] call(String url, String body, String cookie, String agent, int timeoutMs) throws IOException {
        Network n = home();
        if (n == null) throw new IOException("no Wi-Fi");
        HttpURLConnection c = (HttpURLConnection) n.openConnection(new URL(url));
        try {
            c.setConnectTimeout(timeoutMs);
            c.setReadTimeout(timeoutMs);
            c.setInstanceFollowRedirects(false);
            c.setRequestProperty("User-Agent", agent);
            c.setRequestProperty("Accept", "application/json");
            if (cookie != null) c.setRequestProperty("Cookie", cookie);
            if (body != null) {
                byte[] data = body.getBytes(StandardCharsets.UTF_8);
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                c.setFixedLengthStreamingMode(data.length);
                try (OutputStream out = c.getOutputStream()) { out.write(data); }
            }
            int status = c.getResponseCode();
            InputStream in = status >= 400 ? c.getErrorStream() : c.getInputStream();
            ByteArrayOutputStream got = new ByteArrayOutputStream();
            if (in != null) {
                try (InputStream i = in) {
                    byte[] buf = new byte[65536];
                    for (int k; (k = i.read(buf)) > 0; ) got.write(buf, 0, k);
                }
            }
            List<String> cookies = c.getHeaderFields().get("Set-Cookie");
            return new String[]{String.valueOf(status), got.toString("UTF-8"),
                    cookies == null ? "" : String.join("\n", cookies)};
        } finally {
            c.disconnect();
        }
    }
}
