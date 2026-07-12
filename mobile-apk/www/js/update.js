// In-app update check. Only active inside the Android app, where the native
// MyTCGUpdate bridge (see UpdateBridge.java) is present; the browser build has
// no bridge and updates itself normally. On launch it asks GitHub for the
// latest published release and, if that build number is newer than the one
// installed, shows a dismissible banner that opens the APK download in the
// system browser. Everything here is best-effort: any network, permission, or
// parse error is swallowed so offline play is never affected.

const RELEASES_API = 'https://api.github.com/repos/KayKoehle/myTCG/releases/latest';
const DISMISS_KEY = 'mytcg.updateDismissed';

// CI tags each release "v<run_number>"; that integer is the build's versionCode.
function parseVersionCode(tag) {
    const match = /(\d+)/.exec(tag || '');
    return match ? parseInt(match[1], 10) : NaN;
}

function findApkUrl(release) {
    const assets = Array.isArray(release.assets) ? release.assets : [];
    const apk = assets.find((asset) => (asset.name || '').toLowerCase().endsWith('.apk'));
    return apk ? apk.browser_download_url : null;
}

function showBanner(label, apkUrl, remoteCode) {
    if (document.getElementById('updateBanner')) return;
    const bridge = window.MyTCGUpdate;

    const banner = document.createElement('div');
    banner.id = 'updateBanner';
    banner.className = 'update-banner';
    banner.setAttribute('role', 'status');

    const text = document.createElement('span');
    text.className = 'update-banner-text';
    text.textContent = `Update available${label ? ' — ' + label : ''}`;

    const download = document.createElement('button');
    download.type = 'button';
    download.className = 'btn update-banner-download';
    download.textContent = 'Download';
    download.addEventListener('click', () => {
        if (apkUrl && bridge && typeof bridge.openUrl === 'function') {
            bridge.openUrl(apkUrl);
        }
    });

    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'update-banner-close';
    dismiss.setAttribute('aria-label', 'Dismiss update notice');
    dismiss.textContent = '×';
    dismiss.addEventListener('click', () => {
        try {
            localStorage.setItem(DISMISS_KEY, String(remoteCode));
        } catch (err) {
            /* private mode / storage disabled: just close the banner */
        }
        banner.remove();
    });

    banner.append(text, download, dismiss);
    document.body.appendChild(banner);
}

export async function initUpdateCheck() {
    const bridge = window.MyTCGUpdate;
    // Not the Android app (or an older build without the bridge): nothing to do.
    if (!bridge || typeof bridge.versionCode !== 'function' || typeof bridge.openUrl !== 'function') {
        return;
    }

    let localCode = 0;
    try {
        localCode = Number(bridge.versionCode()) || 0;
    } catch (err) {
        return;
    }

    try {
        const res = await fetch(RELEASES_API, { headers: { Accept: 'application/vnd.github+json' } });
        if (!res.ok) return;
        const release = await res.json();

        const remoteCode = parseVersionCode(release.tag_name);
        if (!Number.isFinite(remoteCode) || remoteCode <= localCode) return;

        // Respect an earlier "not now" for this exact version.
        let dismissed = NaN;
        try {
            dismissed = parseInt(localStorage.getItem(DISMISS_KEY) || '', 10);
        } catch (err) {
            /* storage unavailable: fall through and show the banner */
        }
        if (dismissed === remoteCode) return;

        const apkUrl = findApkUrl(release);
        if (!apkUrl) return;

        showBanner(release.name || release.tag_name, apkUrl, remoteCode);
    } catch (err) {
        // Offline, rate-limited, or blocked: stay quiet and let the user play.
    }
}
