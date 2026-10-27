// Load print-media stylesheets (fonts) without blocking render.
// Replaces the inline onload="this.media='all'" so a strict CSP can forbid inline handlers.
function activatePrintStylesheets() {
  document.querySelectorAll('link[media="print"]').forEach(function (link) {
    if (link.sheet) {
      link.media = 'all';                 // already loaded
    } else {
      link.addEventListener('load', function () { link.media = 'all'; });
    }
  });
}

// On some pages this script tag sits above the font <link>, so running straight
// away would find nothing and the font would stay on media="print" forever.
// Wait for the document to finish parsing unless it already has.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', activatePrintStylesheets);
} else {
  activatePrintStylesheets();
}
