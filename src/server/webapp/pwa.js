// Inside the Android app assets are bundled locally, so the service worker
// adds nothing; the native bridge marker tells us we are in the APK.
if ('serviceWorker' in navigator && !window.MyTCGLocalApi) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').catch((error) => {
            console.error('Service worker registration failed:', error);
        });
    });
}
