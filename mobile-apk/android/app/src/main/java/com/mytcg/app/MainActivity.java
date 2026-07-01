package com.mytcg.app;

import android.os.Bundle;

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
	}
}
