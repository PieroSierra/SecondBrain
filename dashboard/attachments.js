/*
 * Image attachments for the query bar and the Paste Markdown card.
 *
 * An AttachmentTray collects images from a file picker, clipboard paste and
 * drag-and-drop, shows each as a thumbnail chip with a remove button, and
 * enforces the same limits as the bridge (/run-multipart).
 */

export const MAX_IMAGES = 10;
export const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
export const MAX_TOTAL_BYTES = 64 * 1024 * 1024;

// Types the bridge accepts as-is. Other image types from the macOS clipboard
// or Finder (HEIC, TIFF, BMP) are converted to PNG in the page.
const DIRECT_TYPES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"]);

function mb(bytes) {
  return `${Math.round(bytes / (1024 * 1024))} MB`;
}

function readDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function toPng(file) {
  const bitmap = await createImageBitmap(file);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  canvas.getContext("2d").drawImage(bitmap, 0, 0);
  bitmap.close?.();
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!blob) throw new Error("conversion failed");
  const stem = (file.name || "image").replace(/\.[^.]+$/, "") || "image";
  return new File([blob], `${stem}.png`, { type: "image/png" });
}

function imageFilesFrom(list) {
  return [...(list || [])].filter((f) => f && f.type && f.type.startsWith("image/"));
}

export class AttachmentTray {
  /**
   * @param {object} opts
   * @param {HTMLElement} opts.tray       Element the chips render into.
   * @param {HTMLButtonElement} [opts.button]  Opens the file picker.
   * @param {HTMLElement} [opts.pasteTarget]   Element whose paste events are watched.
   * @param {HTMLElement} [opts.dropTarget]    Element that accepts dropped images.
   * @param {(msg: string) => void} [opts.onError]
   * @param {(tray: AttachmentTray) => void} [opts.onChange]
   */
  constructor({ tray, button, pasteTarget, dropTarget, onError, onChange }) {
    this.tray = tray;
    this.onError = onError || (() => {});
    this.onChange = onChange || (() => {});
    this.items = []; // { file, url }
    this.disabled = false;

    this.input = document.createElement("input");
    this.input.type = "file";
    this.input.multiple = true;
    this.input.accept = "image/png,image/jpeg,image/gif,image/webp,image/heic,image/tiff";
    this.input.className = "visually-hidden";
    this.input.tabIndex = -1;
    this.input.setAttribute("aria-hidden", "true");
    tray.after(this.input);
    this.input.addEventListener("change", () => {
      const files = [...this.input.files];
      this.input.value = "";
      this.add(files);
    });

    if (button) {
      this.button = button;
      button.addEventListener("click", () => {
        if (!this.disabled) this.input.click();
      });
    }

    if (pasteTarget) {
      pasteTarget.addEventListener("paste", (e) => {
        const data = e.clipboardData;
        if (!data) return;
        const files = imageFilesFrom(
          [...data.items].filter((i) => i.kind === "file").map((i) => i.getAsFile())
        );
        if (!files.length) return;
        // Let text paste through when the clipboard also has text.
        if (!data.types.includes("text/plain")) e.preventDefault();
        this.add(files);
      });
    }

    if (dropTarget) {
      const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
      dropTarget.addEventListener("dragover", (e) => {
        if (!hasFiles(e) || this.disabled) return;
        e.preventDefault();
        dropTarget.classList.add("attach-drop-active");
      });
      dropTarget.addEventListener("dragleave", (e) => {
        if (!dropTarget.contains(e.relatedTarget)) {
          dropTarget.classList.remove("attach-drop-active");
        }
      });
      dropTarget.addEventListener("drop", (e) => {
        dropTarget.classList.remove("attach-drop-active");
        if (!hasFiles(e) || this.disabled) return;
        e.preventDefault();
        e.stopPropagation();
        const files = imageFilesFrom(e.dataTransfer.files);
        if (!files.length) {
          this.onError("Only images can be attached here.");
          return;
        }
        this.add(files);
      });
    }

    this.render();
  }

  get count() {
    return this.items.length;
  }

  files() {
    return this.items.map((item) => item.file);
  }

  /** Snapshot for restoring the tray after a Stop. */
  snapshot() {
    return this.items.slice();
  }

  restore(items) {
    this.items = items.slice();
    this.render();
  }

  clear() {
    this.items = [];
    this.render();
  }

  setDisabled(disabled) {
    this.disabled = disabled;
    if (this.button) this.button.disabled = disabled;
    this.tray.querySelectorAll("button").forEach((b) => { b.disabled = disabled; });
  }

  async add(files) {
    if (this.disabled) return;
    for (let file of files) {
      if (this.items.length >= MAX_IMAGES) {
        this.onError(`You can attach up to ${MAX_IMAGES} images.`);
        break;
      }
      if (!DIRECT_TYPES.has(file.type)) {
        try {
          file = await toPng(file);
        } catch {
          this.onError(`${file.name || "That image"} can't be attached. Use PNG, JPEG, GIF or WebP.`);
          continue;
        }
      }
      if (file.size > MAX_IMAGE_BYTES) {
        this.onError(`${file.name || "Image"} is ${mb(file.size)}. The limit is ${mb(MAX_IMAGE_BYTES)} per image.`);
        continue;
      }
      const total = this.items.reduce((sum, item) => sum + item.file.size, 0);
      if (total + file.size > MAX_TOTAL_BYTES) {
        this.onError(`Attachments are limited to ${mb(MAX_TOTAL_BYTES)} in total.`);
        break;
      }
      let url;
      try {
        url = await readDataUrl(file);
      } catch {
        this.onError(`${file.name || "That image"} could not be read.`);
        continue;
      }
      this.items.push({ file, url });
    }
    this.render();
  }

  remove(index) {
    this.items.splice(index, 1);
    this.render();
  }

  render() {
    this.tray.replaceChildren();
    this.items.forEach((item, index) => {
      const chip = document.createElement("div");
      chip.className = "attach-chip";
      const img = document.createElement("img");
      img.src = item.url;
      img.alt = item.file.name || `Image ${index + 1}`;
      chip.appendChild(img);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "attach-remove";
      remove.textContent = "×";
      remove.title = "Remove image";
      remove.setAttribute("aria-label", `Remove ${img.alt}`);
      remove.disabled = this.disabled;
      remove.addEventListener("click", () => this.remove(index));
      chip.appendChild(remove);
      this.tray.appendChild(chip);
    });
    this.tray.hidden = this.items.length === 0;
    this.onChange(this);
  }
}
