// Register Service Worker for PWA
(function(){
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', function(){
            navigator.serviceWorker.register('/sw.js', { scope: '/' })
                .catch(function(err){ console.warn('SW registration failed', err); });
        });
    }
})();
