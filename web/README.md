# Web

Static site built with Tailwind: the marketing pages, the dashboard and the ally page.
See the repository README for what is built.

## Videos

The public pages have video slots: `<figure data-video="name" hidden></figure>`. `public/video.js` shows a slot only once its video is listed in `public/media/videos.json`, so the site never shows an empty player.

To publish a video called `connect-plaud`:

1. Put three files in `public/media/`: `connect-plaud.mp4` (H.264, 1280 or 1920 wide), `connect-plaud.jpg` (the still shown before it plays, same size) and `connect-plaud.vtt` (captions, required: they are on by default).
2. List it in `public/media/videos.json`:

   ```json
   { "videos": { "connect-plaud": { "title": "Connect your Plaud", "duration": "0:45", "version": "1" } } }
   ```

   Bump `version` when you replace a video, so browsers fetch the new one.
3. Deploy. `*.mp4` files are git-ignored: they are uploaded from the machine that has them, and a deploy never deletes published videos. The `.jpg`, `.vtt` and `videos.json` are committed.

Slots: `how-it-works` on the home page; `create-account`, `agree`, `connect-plaud`, `add-allies`, `ally-confirms`, `first-results` and `this-was-nothing` on `/setup/`.
