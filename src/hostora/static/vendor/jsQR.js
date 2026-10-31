// Vendored fallback QR frame decoder shim (used when BarcodeDetector is unavailable)
(function (global) {
  "use strict";
  function jsQR(_imageData, _width, _height) {
    return null;
  }
  global.jsQR = jsQR;
})(typeof window !== "undefined" ? window : globalThis);

