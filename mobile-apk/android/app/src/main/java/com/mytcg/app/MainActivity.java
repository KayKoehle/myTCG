package com.mytcg.app;

import android.os.Bundle;
import android.webkit.WebView;

import androidx.activity.OnBackPressedCallback;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
	@Override
	public void onCreate(Bundle savedInstanceState) {
		super.onCreate(savedInstanceState);

		if (!Python.isStarted()) {
			Python.start(new AndroidPlatform(this));
		}

		getBridge().getWebView().addJavascriptInterface(new LocalApiBridge(), "MyTCGLocalApi");

		// Capacitor core has no back-button handling (it lives in the
		// @capacitor/app plugin, which we don't ship), so without this the
		// hardware back button closes the activity from anywhere in the app.
		// The webapp drives navigation through history entries (menu.js
		// pushNav/popstate), so walking the WebView history is all it takes;
		// at the root there is nothing left to pop and back backgrounds the
		// app instead of killing it.
		getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
			@Override
			public void handleOnBackPressed() {
				WebView webView = getBridge().getWebView();
				if (webView != null && webView.canGoBack()) {
					webView.goBack();
				} else {
					moveTaskToBack(true);
				}
			}
		});
	}
}
