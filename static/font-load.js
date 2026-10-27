// Load print-media stylesheets (fonts) without blocking render.
// Replaces the inline onload="this.media='all'" so a strict CSP can forbid inline handlers.
document.querySelectorAll('link[media="print"]').forEach(function (link) {
  if (link.sheet) {
    link.media = 'all';                 // already loaded
  } else {
    link.addEventListener('load', function () { link.media = 'all'; });
  }
});
