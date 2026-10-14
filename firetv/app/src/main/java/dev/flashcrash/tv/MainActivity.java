package dev.flashcrash.tv;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Flash Crash for Fire TV: a full-screen 10-foot dashboard of the AI trader market.
 * The D-pad drives the on-screen controls; Play/Pause, Rewind and Fast-forward are shortcuts.
 * Press Menu to change the game server address.
 */
public class MainActivity extends Activity {
    static final String PREFS = "flashcrash";
    static final String KEY_SERVER = "server";

    private WebView web;
    private String loadedServer;

    static String server(Activity a) {
        SharedPreferences p = a.getSharedPreferences(PREFS, MODE_PRIVATE);
        return p.getString(KEY_SERVER, BuildConfig.DEFAULT_SERVER);
    }

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#07090D"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);
        web.setFocusable(true);
        web.setFocusableInTouchMode(true);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) showOffline(err.getDescription().toString());
            }
        });
        setContentView(web);
        hideSystemUi();
    }

    @Override
    protected void onResume() {
        super.onResume();
        String srv = server(this);
        if (!srv.equals(loadedServer)) {
            loadedServer = srv;
            web.loadUrl(srv.replaceAll("/+$", "") + "/tv");
        }
        web.requestFocus();
    }

    private void showOffline(String why) {
        String html = "<html><body style='background:#07090D;color:#e7ecf3;font-family:sans-serif;display:flex;"
                + "align-items:center;justify-content:center;height:100vh;margin:0;text-align:center'><div>"
                + "<h1 style='font-size:48px'>⚡ FLASH <span style='color:#ff4d5e'>CRASH</span></h1>"
                + "<p style='font-size:24px'>Can't reach the game server at<br><b style='color:#ffd23f'>"
                + android.text.Html.escapeHtml(loadedServer) + "</b></p>"
                + "<p style='color:#7d8899;font-size:20px'>" + android.text.Html.escapeHtml(why) + "</p>"
                + "<p style='font-size:22px'>Press <b>OK</b> to retry · <b>Menu</b> to change server</p></div></body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
        web.setTag("offline");
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_MENU) {
            startActivity(new Intent(this, SetupActivity.class));
            return true;
        }
        if ("offline".equals(web.getTag())
                && (keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_ENTER)) {
            web.setTag(null);
            loadedServer = null;
            onResume();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemUi();
    }

    private void hideSystemUi() {
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY);
    }

    @Override
    protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }
}
