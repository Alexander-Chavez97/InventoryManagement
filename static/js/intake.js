(() => {
  const InventoryIntake = {
    scanner: null,

    bind() {
      this.stop();
      const startBtn = document.querySelector("[data-scan-start]");
      const stopBtn = document.querySelector("[data-scan-stop]");
      const cameraShot = document.getElementById("camera-shot");
      const photos = document.getElementById("id_photos");
      if (!photos) return;

      startBtn?.addEventListener("click", () => this.start());
      stopBtn?.addEventListener("click", () => this.stop());
      cameraShot?.addEventListener("change", () => {
        this.mergeFiles(photos, cameraShot.files);
        cameraShot.value = "";
        this.renderPreviews();
      });
      photos.addEventListener("change", () => this.renderPreviews());
      this.renderPreviews();
    },

    mergeFiles(input, incoming) {
      const transfer = new DataTransfer();
      for (const file of input.files) transfer.items.add(file);
      for (const file of incoming) transfer.items.add(file);
      input.files = transfer.files;
    },

    renderPreviews() {
      const box = document.querySelector("[data-photo-previews]");
      const photos = document.getElementById("id_photos");
      if (!box || !photos) return;
      box.replaceChildren();
      for (const file of photos.files) {
        const img = document.createElement("img");
        img.src = URL.createObjectURL(file);
        img.alt = file.name;
        box.appendChild(img);
      }
    },

    async start() {
      const reader = document.getElementById("barcode-reader");
      const status = document.querySelector("[data-scan-status]");
      const stopBtn = document.querySelector("[data-scan-stop]");
      const serial = document.getElementById("id_serial_number");
      if (!reader || typeof Html5Qrcode === "undefined") {
        if (status) status.textContent = "Barcode scanner library did not load.";
        return;
      }
      if (!window.isSecureContext && status) {
        status.textContent =
          "Live camera scan needs HTTPS (or localhost). You can still take a barcode photo below.";
      }
      try {
        this.scanner = new Html5Qrcode("barcode-reader");
        reader.hidden = false;
        if (stopBtn) stopBtn.hidden = false;
        await this.scanner.start(
          { facingMode: "environment" },
          { fps: 10, qrbox: { width: 280, height: 140 } },
          (decoded) => {
            if (serial) serial.value = decoded.trim();
            if (status) status.textContent = "Scanned " + decoded;
            this.stop();
            serial?.focus();
          }
        );
      } catch (error) {
        if (status) {
          status.textContent =
            "Could not open the camera. Use Take photo, or type the serial.";
        }
        this.stop();
      }
    },

    async stop() {
      const reader = document.getElementById("barcode-reader");
      const stopBtn = document.querySelector("[data-scan-stop]");
      if (stopBtn) stopBtn.hidden = true;
      if (this.scanner) {
        try {
          await this.scanner.stop();
          this.scanner.clear();
        } catch (_err) {
          /* already stopped */
        }
        this.scanner = null;
      }
      if (reader) reader.hidden = true;
    },
  };

  window.InventoryIntake = InventoryIntake;
  document.addEventListener("DOMContentLoaded", () => InventoryIntake.bind());
})();
