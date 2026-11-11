package io.github.elias8832.animehub;

import android.app.IntentService;
import android.content.Intent;

/** Where Termux sends a job's answer. */
public class TermuxResult extends IntentService {
    public TermuxResult() { super("TermuxResult"); }

    @Override protected void onHandleIntent(Intent intent) {
        if (intent != null) TermuxBridge.answer(intent.getIntExtra("id", 0), intent.getBundleExtra("result"));
    }
}
