package com.videomanager.phone;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Message;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebViewTransport;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * Full-screen WebView client for a VideoManager server on the local network.
 * The server, database and video files stay on the PC. The phone only renders
 * the web UI. A small native toolbar gives reliable Back / Home / Reload /
 * menu controls even where the responsive web interface hides them.
 */
public class MainActivity extends Activity {
    private static final String PREFS_NAME = "videomanager_phone";
    private static final String PREF_SERVER_URL = "server_url";
    private static final String DEFAULT_SERVER_URL = "http://192.168.1.2:5000/";

    private FrameLayout root;
    private LinearLayout content;
    private WebView webView;
    private WebView popupWebView;
    private View customView;
    private WebChromeClient.CustomViewCallback customViewCallback;
    private SharedPreferences prefs;
    private TextView backButton;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(24, 24, 27));

        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        root.addView(content, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        content.addView(createToolbar(), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(52)));

        webView = new WebView(this);
        content.addView(webView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        setContentView(root);
        configureWebView();
        setTitle("VideoManager");
        hideSystemUi();

        String saved = prefs.getString(PREF_SERVER_URL, "");
        if (saved == null || saved.trim().isEmpty()) {
            showServerDialog(true);
        } else {
            loadUrl(saved);
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private LinearLayout createToolbar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setBackgroundColor(Color.rgb(24, 24, 27));
        bar.setGravity(Gravity.CENTER_VERTICAL);

        backButton = addToolbarButton(bar, "←", "Back", v -> goBack());
        addToolbarButton(bar, "⌂", "Home", v -> loadHome());
        addToolbarButton(bar, "⟳", "Reload", v -> webView.reload());

        View spacer = new View(this);
        bar.addView(spacer, new LinearLayout.LayoutParams(0, dp(52), 1f));

        addToolbarButton(bar, "⋮", "Menu", v -> showMainMenu());
        return bar;
    }

    private TextView addToolbarButton(LinearLayout bar, String glyph, String description,
                                      View.OnClickListener listener) {
        TextView button = new TextView(this);
        button.setText(glyph);
        button.setTextSize(21f);
        button.setTextColor(Color.rgb(230, 231, 234));
        button.setGravity(Gravity.CENTER);
        button.setContentDescription(description);
        button.setBackground(null);
        button.setOnClickListener(listener);
        bar.addView(button, new LinearLayout.LayoutParams(dp(52), dp(52)));
        return button;
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(true);
        settings.setSupportMultipleWindows(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setSupportZoom(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setGeolocationEnabled(false);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return handleUrl(String.valueOf(request.getUrl()));
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return handleUrl(url);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                updateBackButton();
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                if (request != null && request.isForMainFrame()) {
                    Toast.makeText(MainActivity.this,
                            "Cannot reach server: " + error.getDescription(),
                            Toast.LENGTH_LONG).show();
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                showFullscreen(view, callback);
            }

            @Override
            public void onHideCustomView() {
                hideFullscreen();
            }

            /** `target="_blank"` links (e.g. Duplicates → Compare) open in this WebView. */
            @Override
            public boolean onCreateWindow(WebView view, boolean isDialog,
                                          boolean isUserGesture, Message resultMsg) {
                if (popupWebView != null) {
                    popupWebView.destroy();
                }
                popupWebView = new WebView(getApplicationContext());
                popupWebView.setWebViewClient(new WebViewClient() {
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView w, WebResourceRequest r) {
                        return openInMainView(String.valueOf(r.getUrl()));
                    }

                    @Override
                    public boolean shouldOverrideUrlLoading(WebView w, String url) {
                        return openInMainView(url);
                    }
                });
                WebViewTransport transport = (WebViewTransport) resultMsg.obj;
                transport.setWebView(popupWebView);
                resultMsg.sendToTarget();
                return true;
            }
        });
    }

    private boolean openInMainView(String url) {
        if (url == null) return true;
        if (url.startsWith("http://") || url.startsWith("https://")) {
            loadUrl(url);
        } else {
            handleUrl(url);
        }
        return true;
    }

    /** Keep http(s) navigation inside the app; hand other schemes to Android. */
    private boolean handleUrl(String url) {
        if (url == null) return true;
        if (url.startsWith("http://") || url.startsWith("https://")) return false;
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (Exception e) {
            Toast.makeText(this, "Cannot open: " + url, Toast.LENGTH_SHORT).show();
        }
        return true;
    }

    private String normalizeUrl(String raw) {
        String url = raw == null ? "" : raw.trim();
        if (url.isEmpty()) url = DEFAULT_SERVER_URL;
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "http://" + url;
        }
        if (!url.endsWith("/")) url += "/";
        return url;
    }

    private String serverRoot() {
        return normalizeUrl(prefs.getString(PREF_SERVER_URL, DEFAULT_SERVER_URL));
    }

    private void loadUrl(String raw) {
        webView.loadUrl(normalizeUrl(raw));
        updateBackButton();
    }

    private void loadHome() {
        loadUrl(serverRoot());
    }

    private void updateBackButton() {
        if (backButton != null) {
            backButton.setAlpha(webView != null && webView.canGoBack() ? 1f : 0.45f);
        }
    }

    private void goBack() {
        if (customView != null) {
            hideFullscreen();
        } else if (webView.canGoBack()) {
            webView.goBack();
        } else {
            loadHome();
        }
    }

    private void showServerDialog(boolean firstRun) {
        final EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint("http://192.168.1.10:5000");
        input.setText(serverRoot());

        AlertDialog.Builder builder = new AlertDialog.Builder(this)
                .setTitle(firstRun ? "Connect to VideoManager" : "Server address")
                .setMessage("Enter the LAN address shown in VideoManager on your PC.\n"
                        + "Example: http://192.168.1.10:5000/")
                .setView(input)
                .setPositiveButton("Open", (dialog, which) -> {
                    String url = normalizeUrl(input.getText().toString());
                    prefs.edit().putString(PREF_SERVER_URL, url).apply();
                    loadUrl(url);
                });

        if (firstRun) {
            builder.setCancelable(false);
        } else {
            builder.setNegativeButton("Cancel", null);
        }
        builder.show();
    }

    private void showMainMenu() {
        String[] items = {
                "Server address",
                "Settings",
                "Duplicates / photo compare",
                "Photos",
                "Reload",
                "Exit"
        };
        new AlertDialog.Builder(this)
                .setTitle("VideoManager")
                .setItems(items, (dialog, which) -> {
                    switch (which) {
                        case 0:
                            showServerDialog(false);
                            break;
                        case 1:
                            loadUrl(serverRoot() + "settings");
                            break;
                        case 2:
                            loadUrl(serverRoot() + "duplicates");
                            break;
                        case 3:
                            loadUrl(serverRoot() + "photos");
                            break;
                        case 4:
                            webView.reload();
                            break;
                        case 5:
                            finish();
                            break;
                        default:
                            break;
                    }
                })
                .show();
    }

    private void showFullscreen(View view, WebChromeClient.CustomViewCallback callback) {
        if (customView != null) {
            if (callback != null) callback.onCustomViewHidden();
            return;
        }
        customView = view;
        customViewCallback = callback;
        root.addView(view, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        content.setVisibility(View.GONE);
        hideSystemUi();
    }

    private void hideFullscreen() {
        if (customView == null) return;
        root.removeView(customView);
        customView = null;
        content.setVisibility(View.VISIBLE);
        hideSystemUi();

        WebChromeClient.CustomViewCallback callback = customViewCallback;
        customViewCallback = null;
        if (callback != null) callback.onCustomViewHidden();
    }

    private void hideSystemUi() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                controller.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                controller.setSystemBarsBehavior(
                        WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                            | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        }
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemUi();
    }

    @Override
    public void onBackPressed() {
        if (customView != null) {
            hideFullscreen();
            return;
        }
        if (webView.canGoBack()) {
            webView.goBack();
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("Exit VideoManager?")
                .setPositiveButton("Exit", (dialog, which) -> finish())
                .setNegativeButton("Cancel", null)
                .show();
    }

    @Override
    protected void onDestroy() {
        hideFullscreen();
        if (popupWebView != null) {
            popupWebView.destroy();
            popupWebView = null;
        }
        webView.destroy();
        super.onDestroy();
    }
}
