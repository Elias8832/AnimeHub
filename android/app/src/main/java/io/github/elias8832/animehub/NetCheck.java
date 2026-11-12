package io.github.elias8832.animehub;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.RouteInfo;

/**
 * The VPN check for the online-episode warning (Decision 88). Android hides
 * the network list the PC reads, but tells apps about VPNs itself.
 */
public class NetCheck {
    static Context ctx;

    /**
     * The interface of a VPN that carries this phone's internet traffic, "" when
     * none. A VPN that only routes its own network (Tailscale without an exit
     * node) doesn't count, as on the PC: only a route over (nearly) all
     * addresses does — 0.0.0.0/0, or the 0/1 + 128/1 pair some clients use.
     */
    /** May the app read the phone's files? Android's "All files access" (11+), or plain read before. */
    public static boolean files() {
        if (android.os.Build.VERSION.SDK_INT >= 30) return android.os.Environment.isExternalStorageManager();
        return ctx.checkSelfPermission("android.permission.READ_EXTERNAL_STORAGE")
                == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    public static String vpn() {
        ConnectivityManager cm = ctx.getSystemService(ConnectivityManager.class);
        for (Network n : cm.getAllNetworks()) {
            NetworkCapabilities caps = cm.getNetworkCapabilities(n);
            if (caps == null || !caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) continue;
            LinkProperties lp = cm.getLinkProperties(n);
            if (lp == null) continue;
            for (RouteInfo r : lp.getRoutes()) {
                if (r.getDestination().getPrefixLength() <= 1) {
                    String name = lp.getInterfaceName();
                    return name == null || name.isEmpty() ? "vpn" : name;
                }
            }
        }
        return "";
    }
}
