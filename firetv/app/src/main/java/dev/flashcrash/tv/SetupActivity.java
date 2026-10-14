package dev.flashcrash.tv;

import android.app.Activity;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;

/** Lets the player point the TV at the machine running the Flash Crash server. */
public class SetupActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_setup);
        EditText url = findViewById(R.id.server_url);
        url.setText(MainActivity.server(this));
        url.setSelection(url.getText().length());
        url.setOnEditorActionListener((v, actionId, e) -> {
            if (actionId == EditorInfo.IME_ACTION_DONE
                    || (e != null && e.getKeyCode() == KeyEvent.KEYCODE_ENTER)) {
                save(url.getText().toString());
                return true;
            }
            return false;
        });
        Button go = findViewById(R.id.save);
        go.setOnClickListener(v -> save(url.getText().toString()));
    }

    private void save(String value) {
        String v = value.trim();
        if (v.isEmpty()) return;
        if (!v.startsWith("http://") && !v.startsWith("https://")) v = "http://" + v;
        getSharedPreferences(MainActivity.PREFS, MODE_PRIVATE).edit().putString(MainActivity.KEY_SERVER, v).apply();
        finish();
    }
}
