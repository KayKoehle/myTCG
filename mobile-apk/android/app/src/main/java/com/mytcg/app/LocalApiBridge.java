package com.mytcg.app;

import android.webkit.JavascriptInterface;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

public class LocalApiBridge {
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

    private static String escapeJson(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r");
    }
}
