package com.mytcg.app;

import android.content.Context;
import android.net.wifi.WifiManager;
import android.webkit.JavascriptInterface;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

public class LocalApiBridge {
    private final Context appContext;
    // Held only while peer discovery is active (the LAN browse/lobby screen): a
    // MulticastLock is what lets Android deliver the UDP broadcast beacons
    // discovery relies on. It is released the moment a match starts or the LAN
    // screen closes — the radio drain of holding it (and of a full Wi-Fi lock,
    // which we deliberately don't take) isn't worth paying once discovery is
    // done, and a hosted match stays reachable over ordinary TCP without it.
    private WifiManager.MulticastLock multicastLock;

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
    // acquires the multicast lock discovery needs. Idempotent — called every
    // time the LAN screen opens, so it also re-takes the lock after a previous
    // pauseLanDiscovery. Returns the JSON the Python side reports (port to
    // advertise).
    @JavascriptInterface
    public String startLanServer() {
        try {
            acquireMulticastLock();
            Python py = Python.getInstance();
            PyObject module = py.getModule("mobile_api");
            return module.callAttr("start_lan_server").toString();
        } catch (Exception e) {
            String message = e.getMessage() == null ? "Could not start LAN server" : e.getMessage();
            return "{\"ok\":false,\"error\":\"" + escapeJson(message) + "\"}";
        }
    }

    // Releases the discovery lock without tearing down the HTTP server: called
    // when leaving the LAN browse/lobby screen (including into a match), where a
    // hosted game stays reachable but no more broadcasts need to be heard.
    @JavascriptInterface
    public String pauseLanDiscovery() {
        releaseMulticastLock();
        return "{\"ok\":true}";
    }

    @JavascriptInterface
    public String stopLanServer() {
        try {
            releaseMulticastLock();
            Python py = Python.getInstance();
            PyObject module = py.getModule("mobile_api");
            return module.callAttr("stop_lan_server").toString();
        } catch (Exception e) {
            String message = e.getMessage() == null ? "Could not stop LAN server" : e.getMessage();
            return "{\"ok\":false,\"error\":\"" + escapeJson(message) + "\"}";
        }
    }

    private synchronized void acquireMulticastLock() {
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
    }

    private synchronized void releaseMulticastLock() {
        if (multicastLock != null && multicastLock.isHeld()) {
            multicastLock.release();
        }
    }

    private static String escapeJson(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r");
    }
}
