/**
 * Which files count as recordings, and how big one may be.
 *
 * The content type is decided from the extension rather than sniffed, because
 * the collector never decodes audio: it only moves bytes. Transcription and
 * redaction happen on the server, after Parakeet has produced word timestamps
 * that say where the numbers are. See docs/ARCHITECTURE.md section 11.
 */

export const AUDIO_TYPES = Object.freeze({
  ".mp3": "audio/mpeg",
  ".m4a": "audio/mp4",
  ".mp4": "audio/mp4",
  ".wav": "audio/wav",
  ".ogg": "audio/ogg",
  ".opus": "audio/opus",
  ".flac": "audio/flac",
});

// Plaud records for hours at a stretch, so this is generous. It exists to stop
// a mis-dropped video or disk image being uploaded, not to limit real use.
export const MAX_AUDIO_BYTES = 512 * 1024 * 1024;

export function contentTypeFor(fileName) {
  const dot = fileName.lastIndexOf(".");
  if (dot < 0) return null;
  return AUDIO_TYPES[fileName.slice(dot).toLowerCase()] ?? null;
}

export function isRecording(fileName) {
  // Hidden files and editor or sync temporaries are never recordings, even
  // with an audio extension: "._call.m4a" is macOS metadata, not audio.
  if (fileName.startsWith(".") || fileName.startsWith("~")) return false;
  return contentTypeFor(fileName) !== null;
}
