package com.videomanager.phone;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.ColorStateList;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Message;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.animation.AlphaAnimation;
import android.view.animation.Animation;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

/**
 * Full-screen WebView client for a VideoManager server on the local network.
 * The server, database and video files stay on the PC. The phone only renders
 * the web UI. Navigation is provided by a bottom bar, an injected Back button
 * in the web header, and a right-edge swipe gesture.
 */
public class MainActivity extends Activity {
    private static final String PREFS_NAME = "videomanager_phone";
    private static final String PREF_SERVER_URL = "server_url";
    private static final String DEFAULT_SERVER_URL = "http://192.168.1.2:5000/";

    private static final int FILE_CHOOSER_REQUEST = 1001;
    private static final int FILE_PERMISSION_REQUEST = 1002;
    private static final int NOTIFICATION_PERMISSION_REQUEST = 1003;

    private static final String[] NAV_GLYPHS = {"▶", "▣", "▱", "⚙"};
    private static final String[] NAV_LABELS = {"Videos", "Photos", "Folders", "Settings"};

    private static final String INJECT_STYLE = """
            (function () {
                if (document.getElementById('vm-phone-style')) return;
                var style = document.createElement('style');
                style.id = 'vm-phone-style';
                style.textContent = [
                    '#vm-phone-back{height:40px;min-width:40px;margin:0 6px 0 0;padding:0 8px;',
                    'background:rgba(220,53,69,.16);border:1px solid rgba(220,53,69,.45);',
                    'border-radius:8px;color:#fff;font-size:20px;line-height:1;',
                    'display:inline-flex;align-items:center;justify-content:center;',
                    'cursor:pointer;flex-shrink:0;vertical-align:middle}',
                    '#vm-phone-back:hover{background:rgba(220,53,69,.30)}',
                    '#vm-phone-back.vm-phone-fixed{position:fixed;top:10px;right:10px;',
                    'z-index:3000;margin:0;background-color:#18181B}',
                    '.top-bar .right-section{gap:4px}'
                ].join('');
                document.head.appendChild(style);
            })();
            """;

    private static final String INJECT_SCRIPT = """
            (function () {
                function call(name, value) {
                    if (window.VMPhone && typeof window.VMPhone[name] === 'function') {
                        try { window.VMPhone[name](value); } catch (e) {}
                    }
                }
                function addBackButton() {
                    if (document.getElementById('vm-phone-back')) return;
                    var button = document.createElement('button');
                    button.type = 'button';
                    button.id = 'vm-phone-back';
                    button.textContent = '←';
                    button.title = 'Back';
                    button.setAttribute('aria-label', 'Back');
                    button.addEventListener('click', function (event) {
                        event.preventDefault();
                        call('goBack');
                    });
                    var bar = document.querySelector('.top-bar');
                    if (bar) {
                        var right = bar.querySelector('.right-section') || bar;
                        right.insertBefore(button, right.firstChild);
                    } else {
                        button.className = 'vm-phone-fixed';
                        document.body.appendChild(button);
                    }
                }
                function bindVideo(video) {
                    if (video.__vmPhoneBound) return;
                    video.__vmPhoneBound = true;
                    video.addEventListener('waiting', function () { call('setVideoLoading', true); });
                    video.addEventListener('stalled', function () { call('setVideoLoading', true); });
                    video.addEventListener('playing', function () { call('setVideoLoading', false); });
                    video.addEventListener('canplay', function () { call('setVideoLoading', false); });
                    video.addEventListener('loadeddata', function () { call('setVideoLoading', false); });
                    video.addEventListener('error', function () { call('setVideoLoading', false); });
                }
                function bindVideos() {
                    document.querySelectorAll('video').forEach(bindVideo);
                }
                addBackButton();
                bindVideos();
                if (window.MutationObserver) {
                    new MutationObserver(bindVideos).observe(document.documentElement, {
                        childList: true,
                        subtree: true
                    });
                }
            })();
            """;

    private FrameLayout root;
    private LinearLayout content;
    private WebView webView;
    private WebView popupWebView;
    private View customView;
    private WebChromeClient.CustomViewCallback customViewCallback;
    private SharedPreferences prefs;

    private final TextView[] navGlyphs = new TextView[4];
    private final TextView[] navLabels = new TextView[4];
    private int activeTab = 0;

    private FrameLayout loadingOverlay;
    private TextView loadingText;
    private ProgressBar loadingSpinner;
    private LinearLayout errorOverlay;
    private TextView errorDetail;
    private EditText errorInput;
    private View errorIcon;
    private boolean errorVisible = false;
    private int pageLoadingCount = 0;
    private boolean videoLoading = false;

    private ValueCallback<Uri[]> filePathCallback;
    private Intent pendingChooserIntent;

    private float swipeStartX;
    private float swipeStartY;
    private long swipeStartTime;
    private boolean swipeArmed;

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

        webView = new WebView(this);
        content.addView(webView, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        content.addView(createBottomBar(), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(62)));

        loadingOverlay = createLoadingOverlay();
        root.addView(loadingOverlay, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        errorOverlay = createErrorOverlay();
        root.addView(errorOverlay, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        setContentView(root);
        configureWebView();
        hideSystemUi();
        requestNotificationPermissionIfNeeded();

        String saved = prefs.getString(PREF_SERVER_URL, "");
        if (saved == null || saved.trim().isEmpty()) {
            showServerDialog(true);
        } else {
            loadSite(saved);
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private LinearLayout createBottomBar() {
        LinearLayout outer = new LinearLayout(this);
        outer.setOrientation(LinearLayout.VERTICAL);
        outer.setBackgroundColor(Color.rgb(18, 18, 20));

        View divider = new View(this);
        divider.setBackgroundColor(Color.rgb(42, 42, 46));
        outer.addView(divider, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 1));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);

        for (int i = 0; i < NAV_LABELS.length; i++) {
            final int index = i;
            LinearLayout item = new LinearLayout(this);
            item.setOrientation(LinearLayout.VERTICAL);
            item.setGravity(Gravity.CENTER);
            item.setPadding(0, dp(6), 0, dp(6));

            TextView glyph = new TextView(this);
            glyph.setText(NAV_GLYPHS[i]);
            glyph.setTextSize(20f);
            glyph.setGravity(Gravity.CENTER);
            navGlyphs[i] = glyph;
            item.addView(glyph, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT));

            TextView label = new TextView(this);
            label.setText(NAV_LABELS[i]);
            label.setTextSize(11f);
            label.setGravity(Gravity.CENTER);
            label.setPadding(0, dp(2), 0, 0);
            navLabels[i] = label;
            item.addView(label, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT));

            item.setOnClickListener(v -> navigateTab(index));
            item.setOnLongClickListener(v -> {
                if (index == 3) {
                    showServerDialog(false);
                    return true;
                }
                return false;
            });

            row.addView(item, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f));
        }

        outer.addView(row, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(61)));
        updateNavColors();
        return outer;
    }

    private void updateNavColors() {
        for (int i = 0; i < navLabels.length; i++) {
            int color = i == activeTab ? Color.rgb(220, 53, 69) : Color.rgb(184, 187, 196);
            navGlyphs[i].setTextColor(color);
            navLabels[i].setTextColor(color);
        }
    }

    private void setActiveTab(String url) {
        String value = url == null ? "" : url.toLowerCase();
        int tab = 0;
        if (value.contains("/photos") || value.contains("/photo/")) {
            tab = 1;
        } else if (value.contains("/folders") || value.contains("/folder_videos")) {
            tab = 2;
        } else if (value.contains("/settings") || value.contains("/tools")
                || value.contains("/duplicates") || value.contains("/tasks")
                || value.contains("transfer")) {
            tab = 3;
        }
        if (tab != activeTab) {
            activeTab = tab;
            updateNavColors();
        }
    }

    private void navigateTab(int index) {
        activeTab = index;
        updateNavColors();
        switch (index) {
            case 1:
                loadSite(serverRoot() + "photos");
                break;
            case 2:
                loadSite(serverRoot() + "folders");
                break;
            case 3:
                loadSite(serverRoot() + "settings");
                break;
            case 0:
            default:
                loadHome();
                break;
        }
    }

    private FrameLayout createLoadingOverlay() {
        FrameLayout overlay = new FrameLayout(this);
        overlay.setBackgroundColor(Color.argb(175, 0, 0, 0));
        overlay.setVisibility(View.GONE);

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);

        loadingSpinner = new ProgressBar(this, null, android.R.attr.progressBarStyleLarge);
        loadingSpinner.setIndeterminateTintList(ColorStateList.valueOf(Color.rgb(220, 53, 69)));
        box.addView(loadingSpinner, new LinearLayout.LayoutParams(
                dp(64), dp(64)));

        loadingText = new TextView(this);
        loadingText.setText("Connecting...");
        loadingText.setTextColor(Color.rgb(235, 235, 238));
        loadingText.setTextSize(15f);
        loadingText.setGravity(Gravity.CENTER);
        loadingText.setPadding(0, dp(18), 0, 0);
        AlphaAnimation pulse = new AlphaAnimation(0.5f, 1f);
        pulse.setDuration(850);
        pulse.setRepeatMode(Animation.REVERSE);
        pulse.setRepeatCount(Animation.INFINITE);
        loadingText.startAnimation(pulse);
        box.addView(loadingText, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        overlay.addView(box, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.CENTER));
        return overlay;
    }

    private LinearLayout createErrorOverlay() {
        LinearLayout overlay = new LinearLayout(this);
        overlay.setOrientation(LinearLayout.VERTICAL);
        overlay.setGravity(Gravity.CENTER);
        overlay.setBackgroundColor(Color.rgb(24, 24, 27));
        overlay.setBackgroundResource(R.drawable.error_gradient);
        overlay.setPadding(dp(32), dp(40), dp(32), dp(40));
        overlay.setVisibility(View.GONE);

        errorIcon = new TextView(this);
        errorIcon.setText("⚠");
        errorIcon.setTextSize(52f);
        errorIcon.setTextColor(Color.rgb(220, 53, 69));
        errorIcon.setGravity(Gravity.CENTER);
        AlphaAnimation pulse = new AlphaAnimation(0.65f, 1f);
        pulse.setDuration(1100);
        pulse.setRepeatMode(Animation.REVERSE);
        pulse.setRepeatCount(Animation.INFINITE);
        errorIcon.startAnimation(pulse);
        overlay.addView(errorIcon, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView title = new TextView(this);
        title.setText("Server unavailable");
        title.setTextColor(Color.rgb(255, 255, 255));
        title.setTextSize(23f);
        title.setGravity(Gravity.CENTER);
        title.setPadding(0, dp(18), 0, dp(8));
        overlay.addView(title, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView message = new TextView(this);
        message.setText("VideoManager on the PC could not be reached.\n"
                + "Check that it is running and that both devices use the same Wi‑Fi.");
        message.setTextColor(Color.rgb(184, 187, 196));
        message.setTextSize(14f);
        message.setGravity(Gravity.CENTER);
        overlay.addView(message, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        errorDetail = new TextView(this);
        errorDetail.setTextColor(Color.rgb(220, 53, 69));
        errorDetail.setTextSize(13f);
        errorDetail.setGravity(Gravity.CENTER);
        errorDetail.setPadding(0, dp(14), 0, dp(10));
        overlay.addView(errorDetail, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        errorInput = new EditText(this);
        errorInput.setSingleLine(true);
        errorInput.setHint("http://192.168.1.10:5000");
        errorInput.setTextColor(Color.rgb(235, 235, 238));
        errorInput.setHintTextColor(Color.rgb(110, 112, 120));
        overlay.addView(errorInput, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        Button connect = new Button(this);
        connect.setText("Connect");
        connect.setOnClickListener(v -> {
            String url = normalizeUrl(errorInput.getText().toString());
            prefs.edit().putString(PREF_SERVER_URL, url).apply();
            hideError();
            loadSite(url);
        });
        overlay.addView(connect, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                0f));

        TextView tip = new TextView(this);
        tip.setText("Tip: long press Settings in the bottom bar to change the address anytime.");
        tip.setTextColor(Color.rgb(130, 132, 140));
        tip.setTextSize(12f);
        tip.setGravity(Gravity.CENTER);
        tip.setPadding(0, dp(16), 0, 0);
        overlay.addView(tip, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        return overlay;
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

        webView.addJavascriptInterface(new PhoneBridge(), "VMPhone");
        webView.setOnTouchListener(edgeSwipeListener());

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
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                hideError();
                pageLoadingCount = 1;
                updateLoadingOverlay();
                setActiveTab(url);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                pageLoadingCount = 0;
                updateLoadingOverlay();
                setActiveTab(url);
                view.evaluateJavascript(INJECT_STYLE, null);
                view.evaluateJavascript(INJECT_SCRIPT, null);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                if (request != null && request.isForMainFrame()) {
                    pageLoadingCount = 0;
                    showConnectionError(String.valueOf(error.getDescription()));
                }
            }

            @Override
            public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                            WebResourceResponse response) {
                if (request != null && request.isForMainFrame()
                        && response != null && response.getStatusCode() >= 500) {
                    pageLoadingCount = 0;
                    showConnectionError("HTTP " + response.getStatusCode());
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
                WebView.WebViewTransport transport = (WebView.WebViewTransport) resultMsg.obj;
                transport.setWebView(popupWebView);
                resultMsg.sendToTarget();
                return true;
            }

            /** File Transfer upload inputs need a native file picker. */
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = callback;

                Intent intent;
                try {
                    if (params != null && params.isDirMode()) {
                        intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                        intent.addCategory(Intent.CATEGORY_OPENABLE);
                        intent.setType("*/*");
                        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                    } else if (params != null) {
                        intent = params.createIntent();
                        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                    } else {
                        intent = new Intent(Intent.ACTION_GET_CONTENT);
                        intent.setType("*/*");
                        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                    }
                } catch (Exception e) {
                    filePathCallback.onReceiveValue(null);
                    filePathCallback = null;
                    return true;
                }

                pendingChooserIntent = Intent.createChooser(intent, "Select files");
                if (Build.VERSION.SDK_INT <= 32
                        && checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE)
                        != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(
                            new String[]{Manifest.permission.READ_EXTERNAL_STORAGE},
                            FILE_PERMISSION_REQUEST);
                } else {
                    launchFileChooser();
                }
                return true;
            }
        });

        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) -> {
            if (url == null || (!url.startsWith("http://") && !url.startsWith("https://"))) {
                handleUrl(url);
                return;
            }
            try {
                DownloadManager manager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
                String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
                DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
                String cookie = CookieManager.getInstance().getCookie(url);
                if (cookie != null && !cookie.isEmpty()) {
                    request.addRequestHeader("Cookie", cookie);
                }
                request.setTitle(name);
                request.setDescription("VideoManager download");
                request.setNotificationVisibility(
                        DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
                request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name);
                manager.enqueue(request);
                Toast.makeText(this, "Downloading: " + name, Toast.LENGTH_SHORT).show();
            } catch (Exception e) {
                Toast.makeText(this, "Download failed: " + e.getMessage(), Toast.LENGTH_LONG).show();
            }
        });
    }

    private void launchFileChooser() {
        if (pendingChooserIntent == null) return;
        try {
            startActivityForResult(pendingChooserIntent, FILE_CHOOSER_REQUEST);
        } catch (Exception e) {
            if (filePathCallback != null) {
                filePathCallback.onReceiveValue(null);
                filePathCallback = null;
            }
            pendingChooserIntent = null;
            Toast.makeText(this, "Cannot open file picker: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != FILE_CHOOSER_REQUEST || filePathCallback == null) return;

        Uri[] results = null;
        if (resultCode == RESULT_OK && data != null) {
            ClipData clip = data.getClipData();
            if (clip != null) {
                results = new Uri[clip.getItemCount()];
                for (int i = 0; i < clip.getItemCount(); i++) {
                    results[i] = clip.getItemAt(i).getUri();
                }
            } else if (data.getData() != null) {
                results = new Uri[]{data.getData()};
            }
        }
        filePathCallback.onReceiveValue(results);
        filePathCallback = null;
        pendingChooserIntent = null;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        if (requestCode == FILE_PERMISSION_REQUEST) {
            boolean granted = grantResults.length > 0
                    && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            if (granted) {
                launchFileChooser();
            } else {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                    filePathCallback = null;
                }
                pendingChooserIntent = null;
                Toast.makeText(this, "File access is needed to send files", Toast.LENGTH_LONG).show();
            }
            return;
        }
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
    }

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{Manifest.permission.POST_NOTIFICATIONS},
                    NOTIFICATION_PERMISSION_REQUEST);
        }
    }

    private View.OnTouchListener edgeSwipeListener() {
        return (view, event) -> {
            if (customView != null || errorVisible) return false;
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    swipeArmed = event.getX() >= view.getWidth() - dp(72);
                    swipeStartX = event.getX();
                    swipeStartY = event.getY();
                    swipeStartTime = System.currentTimeMillis();
                    break;
                case MotionEvent.ACTION_UP:
                    if (swipeArmed) {
                        float deltaX = event.getX() - swipeStartX;
                        float deltaY = event.getY() - swipeStartY;
                        long elapsed = System.currentTimeMillis() - swipeStartTime;
                        swipeArmed = false;
                        if (deltaX < -dp(90) && Math.abs(deltaY) < dp(140)
                                && elapsed < 1200) {
                            goBack();
                            return true;
                        }
                    }
                    break;
                case MotionEvent.ACTION_CANCEL:
                    swipeArmed = false;
                    break;
                default:
                    break;
            }
            return false;
        };
    }

    private boolean openInMainView(String url) {
        if (url == null) return true;
        if (url.startsWith("http://") || url.startsWith("https://")) {
            loadSite(url);
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

    private void loadSite(String raw) {
        webView.loadUrl(normalizeUrl(raw));
    }

    private void loadHome() {
        navigateTab(0);
    }

    private void hideError() {
        errorVisible = false;
        errorOverlay.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
    }

    private void showConnectionError(String detail) {
        errorVisible = true;
        errorDetail.setText(detail == null ? "Connection failed" : detail);
        errorInput.setText(serverRoot());
        errorOverlay.setVisibility(View.VISIBLE);
        loadingOverlay.setVisibility(View.GONE);
    }

    private void updateLoadingOverlay() {
        if (errorVisible) {
            loadingOverlay.setVisibility(View.GONE);
            return;
        }
        boolean show = pageLoadingCount > 0 || videoLoading;
        loadingOverlay.setVisibility(show ? View.VISIBLE : View.GONE);
        loadingText.setText(videoLoading ? "Loading video..." : "Connecting...");
    }

    private void goBack() {
        if (customView != null) {
            hideFullscreen();
        } else if (webView.canGoBack()) {
            webView.goBack();
        } else {
            navigateTab(0);
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
                    hideError();
                    loadSite(url);
                });

        if (firstRun) {
            builder.setCancelable(false);
        } else {
            builder.setNegativeButton("Cancel", null);
        }
        builder.show();
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
        if (filePathCallback != null) {
            filePathCallback.onReceiveValue(null);
            filePathCallback = null;
        }
        if (popupWebView != null) {
            popupWebView.destroy();
            popupWebView = null;
        }
        webView.destroy();
        super.onDestroy();
    }

    private class PhoneBridge {
        @JavascriptInterface
        public void goBack() {
            runOnUiThread(MainActivity.this::goBack);
        }

        @JavascriptInterface
        public void setVideoLoading(boolean loading) {
            runOnUiThread(() -> {
                videoLoading = loading;
                updateLoadingOverlay();
            });
        }
    }
}
