package com.mytcg.app;

import android.content.Context;
import android.net.wifi.WifiManager;
import android.webkit.JavascriptInterface;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

public class LocalApiBridge {
    private final Context appContext;
    // Held while LAN play is active so Android delivers the UDP broadcast beacons
    // discovery relies on (Wi-Fi drops multicast/broadcast to sleep the radio
    // unless a lock is held) and keeps the Wi-Fi radio awake for the HTTP server.
    private WifiManager.MulticastLock multicastLock;
    private WifiManager.WifiLock wifiLock;

    public LocalApiBridge(Context context) {
        this.appContext = context.getApplicationContext();
    }

    @JavascriptInterface
    public String postJson(String url, String bodyJson) {
        try {
            Python py = Python.getInstance();
            PyObject module = py.getModule("mobile_api");
            PyObject result = module.callAttr("handle_post_json", url, bodyJson);
            return result.toString();
        } catch (Exception e) {
            String message = e.getMessage() == null ? "Unknown local API error" : e.getMessage();
            return "{\"ok\":false,\"error\":\"" + escapeJson(message) + "\"}";
        }
    }

    // Starts the in-process HTTP server (so LAN peers can reach this device) and
    // acquires the Wi-Fi/multicast locks discovery needs. Returns the JSON the
    // Python side reports, including the port to advertise.
    @JavascriptInterface
    public String startLanServer() {
        try {
            acquireLocks();
            Python py = Python.getInstance();
            PyObject module = py.getModule("mobile_api");
            return module.callAttr("start_lan_server").toString();
        } catch (Exception e) {
            String message = e.getMessage() == null ? "Could not start LAN server" : e.getMessage();
            return "{\"ok\":false,\"error\":\"" + escapeJson(message) + "\"}";
        }
    }

    @JavascriptInterface
    public String stopLanServer() {
        try {
            Python py = Python.getInstance();
            PyObject module = py.getModule("mobile_api");
            String result = module.callAttr("stop_lan_server").toString();
            releaseLocks();
            return result;
        } catch (Exception e) {
            String message = e.getMessage() == null ? "Could not stop LAN server" : e.getMessage();
            return "{\"ok\":false,\"error\":\"" + escapeJson(message) + "\"}";
        }
    }

    private synchronized void acquireLocks() {
        WifiManager wifi = (WifiManager) appContext.getSystemService(Context.WIFI_SERVICE);
        if (wifi == null) {
            return;
        }
        if (multicastLock == null) {
            multicastLock = wifi.createMulticastLock("mytcg-lan");
            multicastLock.setReferenceCounted(false);
        }
        if (!multicastLock.isHeld()) {
            multicastLock.acquire();
        }
        if (wifiLock == null) {
            wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "mytcg-lan");
            wifiLock.setReferenceCounted(false);
        }
        if (!wifiLock.isHeld()) {
            wifiLock.acquire();
        }
    }

    private synchronized void releaseLocks() {
        if (multicastLock != null && multicastLock.isHeld()) {
            multicastLock.release();
        }
        if (wifiLock != null && wifiLock.isHeld()) {
            wifiLock.release();
        }
    }

    private static String escapeJson(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r");
    }
}
