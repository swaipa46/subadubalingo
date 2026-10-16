# SubaDubaLingo website

This is the static site for https://subadubalingo.swaipa.dk/. It is plain HTML, CSS and a little vanilla JS. There
is no build step, no framework, no CDN, no trackers and no external fonts.

## Files

| File | What it is |
|---|---|
| `index.html` | The page, with the Danish texts written in, so it reads fine without JS. |
| `i18n.js` | All texts in DA, SV, NO and EN. Each text has a key, and the `data-i18n*` attributes in `index.html` point to those keys. |
| `site.js` | The theme picker, the language picker and the latest-release check. |
| `style.css` | The layout. The colours are the app's tokens (`assets/ui/tokens.json`). |
| `fonts/` | Press Start 2P and VT323 as subset woff2, with their OFL licences. |
| `img/` | The screenshots in both themes (`<name>-light.webp`, `<name>-dark.webp`), the logo, the icons and `og-image.png` (1200×630, used for sharing). |
| `favicon.ico` | A copy of `assets/branding/SubaDubaLingo.ico`. |

## How it behaves

### Theme

The page follows the visitor's light/dark setting. The sun, moon and monitor buttons override it, and the choice is
saved in `localStorage` (`sdl-theme`).

### Language

The language comes from the browser: da, sv, nb/nn/no and en are supported, and anything else gets English. The
DA SV NO EN buttons override it, and the choice is saved in `localStorage` (`sdl-lang`).

### Screenshots

Each screenshot exists twice, one per theme. CSS hides the one that does not match the theme. The images use
`loading="lazy"`, so the browser only downloads the visible theme's screenshots.

### Download button

It links to `https://github.com/swaipa46/subadubalingo/releases/latest/download/SubaDubaLingo.zip`. On load,
`site.js` asks `api.github.com/repos/swaipa46/subadubalingo/releases/latest`:

- **200:** the latest version from `tag_name` (a leading `v` is dropped) is shown under the button and in the footer.
- **404:** there is no release yet. The page shows "Første version udkommer snart", and both download buttons link to
  the GitHub page.
- **Offline or rate-limited:** nothing changes, and the plain download link stays.

When a release exists, nothing on the site needs updating. Just make sure the release has an asset named exactly
`SubaDubaLingo.zip`.

**Fallback version:** the footer shows `FALLBACK_VERSION` in `site.js` (now 2.0.0) when the API cannot be reached. Bump
it when you like.

## Updating

### Texts

1. Change the text in `i18n.js` for all four languages.
2. If you changed a Danish text, change it in `index.html` as well. Search for the `data-i18n="<key>"` attribute.

### A new text

1. Add the key to all four languages in `i18n.js`.
2. Put the Danish text in `index.html` on an element with the matching attribute:
   - `data-i18n="key"` for the element's text;
   - `data-i18n-alt` for an image's alt text;
   - `data-i18n-title` for a title attribute;
   - `data-i18n-aria` for an aria-label.

### Screenshots

The images come from the app's screenshots in `design/implemented/`. For each one:

1. Cut off the Windows title bar (top 31 px) and the 8 px window edge.
2. Crop to the part that matters.
3. Save as WebP at quality 80, at most 1100-1400 px wide.

Keep the same file names and update `width` and `height` on the `<img>` tags. The source of this round's crops is
`design/website/source/prep.py`.

### New features

Copy one `<article class="feature">` block in `index.html`. Add its texts to `i18n.js` and its two images to `img/`.
`feature-wide` makes a block span the full width.

## Deploy

Copy the folder's contents to `/var/www/subadubalingo` (Caddy `file_server`). Long caching suits the fonts and
images. If you change an image or `style.css`, `site.js` or `i18n.js` and want visitors to get it immediately, give
the file a new name or add `?v=2` to its link.

A Caddy block that does this:

```
subadubalingo.swaipa.dk {
	root * /var/www/subadubalingo
	encode zstd gzip
	@static path /fonts/* /img/*
	header @static Cache-Control "public, max-age=2592000"
	file_server
}
```

## Page weight

The first load is about 100 KB: the HTML, CSS, JS, one font and the logo. All the screenshots for one theme come to
about 300 KB and load as the visitor scrolls.

## Checked

- Contrast uses the app's tokens, which are checked against WCAG AA.
- Every image has an alt text in all four languages.
- The skip link, the pickers, the download buttons and the FAQ all work with the keyboard and have a visible focus
  ring.
- Blinking and smooth scrolling stop when the visitor asks for reduced motion.
