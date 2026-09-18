// ytframe: grab one still frame from a YouTube video at a timestamp.
//
// It is the private half of the "Steal the Moves" pack. It never downloads the
// whole video: it asks the bundled yt-dlp for a direct stream URL, then asks the
// bundled ffmpeg to seek to the timestamp and decode a single frame. Both helper
// binaries sit next to this executable in the pack's bin/ folder.
//
// YouTube answers datacenter addresses with a "sign in to confirm you're not a
// bot" page. Three answers, in order: (1) a cookies.txt next to the binary (or
// $YTMOVE_COOKIES), which a private pack can carry because nobody can fetch it;
// (2) yt-dlp with several player clients; (3) public Invidious / Piped instances,
// which proxy the stream through their own address.
//
//	ytframe -url https://youtu.be/... -time 1:23.5 -out /tmp/frame.png [-height 720] [-burst 0]
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

func main() {
	url := flag.String("url", "", "YouTube URL (or any yt-dlp supported URL, or a direct media URL)")
	at := flag.String("time", "0", "timestamp: seconds, mm:ss or hh:mm:ss, decimals allowed")
	out := flag.String("out", "frame.png", "output PNG path")
	height := flag.Int("height", 720, "max stream height to pick")
	burst := flag.Int("burst", 0, "also write N frames after the timestamp (out_001.png ...)")
	flag.Parse()
	if *url == "" {
		fail("missing -url")
	}
	secs, err := parseTime(*at)
	if err != nil {
		fail(err.Error())
	}
	here, _ := os.Executable()
	bin := filepath.Dir(here)

	var candidates []string
	if looksDirect(*url) {
		candidates = []string{*url}
	} else {
		var errs []string
		if os.Getenv("YTMOVE_FORCE_FALLBACK") == "" {
			s, err := resolveStream(filepath.Join(bin, "yt-dlp"), bin, *url, *height)
			if err == nil {
				candidates = append(candidates, s)
			} else {
				errs = append(errs, "yt-dlp: "+err.Error())
			}
		}
		if len(candidates) == 0 {
			alt, err := resolveViaFrontends(*url, *height)
			if err != nil {
				errs = append(errs, "frontends: "+err.Error())
			}
			candidates = append(candidates, alt...)
		}
		if len(candidates) == 0 {
			fail(strings.Join(errs, " | "))
		}
	}
	// The bundled ffmpeg first; if it dies (a static build can crash inside a hardened
	// container) fall back to whatever ffmpeg the image itself ships on PATH.
	ffmpegs := []string{filepath.Join(bin, "ffmpeg")}
	if p, err := exec.LookPath("ffmpeg"); err == nil && p != ffmpegs[0] {
		ffmpegs = append(ffmpegs, p)
	}
	var errs []string
	for _, stream := range candidates {
		for _, ff := range ffmpegs {
			if err := grab(ff, stream, secs, *out, *burst); err != nil {
				errs = append(errs, filepath.Base(filepath.Dir(ff))+"/ffmpeg: "+err.Error())
				continue
			}
			fmt.Println(*out)
			return
		}
	}
	fail(strings.Join(errs, " | "))
}

func fail(msg string) {
	fmt.Fprintln(os.Stderr, "ytframe: "+msg)
	os.Exit(1)
}

var directRe = regexp.MustCompile(`\.(mp4|webm|mkv|mov|m3u8)(\?|$)`)

func looksDirect(u string) bool {
	return directRe.MatchString(strings.ToLower(u)) && !strings.Contains(u, "youtube.com") && !strings.Contains(u, "youtu.be")
}

// parseTime accepts "83", "83.5", "1:23", "1:23.5", "0:01:23".
func parseTime(s string) (float64, error) {
	s = strings.TrimSpace(s)
	parts := strings.Split(s, ":")
	if len(parts) > 3 {
		return 0, errors.New("bad timestamp " + s)
	}
	total := 0.0
	for _, p := range parts {
		v, err := strconv.ParseFloat(strings.TrimSpace(p), 64)
		if err != nil {
			return 0, errors.New("bad timestamp " + s)
		}
		total = total*60 + v
	}
	if total < 0 {
		return 0, errors.New("negative timestamp")
	}
	return total, nil
}

func cookiesFile(bin string) string {
	if p := os.Getenv("YTMOVE_COOKIES"); p != "" {
		return p
	}
	p := filepath.Join(bin, "cookies.txt")
	if _, err := os.Stat(p); err == nil {
		return p
	}
	return ""
}

// resolveStream asks yt-dlp for a direct URL to a single stream no taller than
// height. Cookies first if the pack carries them; then several player clients,
// because YouTube answers datacenter addresses differently per client.
func resolveStream(ytdlp, bin, url string, height int) (string, error) {
	format := fmt.Sprintf("bv*[height<=%d][ext=mp4]/bv*[height<=%d]/b[height<=%d]/b", height, height, height)
	clients := []string{"", "tv", "mweb", "android", "ios", "web_embedded"}
	cookies := cookiesFile(bin)
	var lastErr error
	for _, c := range clients {
		args := []string{"--no-warnings", "--no-playlist", "--socket-timeout", "20", "-g", "-f", format}
		if cookies != "" {
			args = append(args, "--cookies", cookies)
		}
		if c != "" {
			args = append(args, "--extractor-args", "youtube:player_client="+c)
		}
		args = append(args, url)
		cmd := exec.Command(ytdlp, args...)
		var stdout, stderr bytes.Buffer
		cmd.Stdout, cmd.Stderr = &stdout, &stderr
		if err := cmd.Run(); err != nil {
			lastErr = errors.New(strings.TrimSpace(firstLine(stderr.String())))
			continue
		}
		for _, line := range strings.Split(stdout.String(), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "http") {
				return line, nil
			}
		}
		lastErr = errors.New("no stream url in output")
	}
	if lastErr == nil {
		lastErr = errors.New("unknown")
	}
	return "", lastErr
}

var idRe = regexp.MustCompile(`(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})`)

func videoID(u string) string {
	m := idRe.FindStringSubmatch(u)
	if len(m) == 2 {
		return m[1]
	}
	return ""
}

var invidious = []string{"https://inv.nadeko.net", "https://yewtu.be", "https://invidious.nerdvpn.de", "https://inv.tux.pizza", "https://invidious.privacyredirect.com", "https://iv.duti.dev"}
var piped = []string{"https://pipedapi.kavin.rocks", "https://api.piped.private.coffee", "https://pipedapi.adminforge.de", "https://pipedapi.drgns.space"}

// resolveViaFrontends asks public Invidious / Piped instances for a proxied
// stream URL. Their proxies fetch from YouTube with the instance's address, so
// the datacenter check does not apply to us. Returns every candidate we found.
func resolveViaFrontends(u string, height int) ([]string, error) {
	id := videoID(u)
	if id == "" {
		return nil, errors.New("no video id in url")
	}
	client := &http.Client{Timeout: 15 * time.Second}
	var found []string
	var errs []string
	// Invidious: /latest_version proxies the chosen itag through the instance (local=true). itag 22 = 720p mp4, 18 = 360p mp4.
	for _, inst := range invidious {
		for _, itag := range []string{"22", "18"} {
			if height < 720 && itag == "22" {
				continue
			}
			candidate := fmt.Sprintf("%s/latest_version?id=%s&itag=%s&local=true", inst, id, itag)
			req, _ := http.NewRequest("HEAD", candidate, nil)
			req.Header.Set("User-Agent", "Mozilla/5.0")
			resp, err := client.Do(req)
			if err != nil {
				errs = append(errs, inst+": "+err.Error())
				break
			}
			resp.Body.Close()
			if resp.StatusCode == 200 && strings.HasPrefix(resp.Header.Get("Content-Type"), "video/") {
				found = append(found, candidate)
			} else {
				errs = append(errs, fmt.Sprintf("%s: itag %s -> %d", inst, itag, resp.StatusCode))
			}
		}
	}
	// Piped: /streams/{id} lists proxied videoStreams.
	for _, inst := range piped {
		req, _ := http.NewRequest("GET", inst+"/streams/"+id, nil)
		req.Header.Set("User-Agent", "Mozilla/5.0")
		resp, err := client.Do(req)
		if err != nil {
			errs = append(errs, inst+": "+err.Error())
			continue
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
		resp.Body.Close()
		if resp.StatusCode != 200 {
			errs = append(errs, fmt.Sprintf("%s: %d", inst, resp.StatusCode))
			continue
		}
		var data struct {
			VideoStreams []struct {
				URL       string `json:"url"`
				Format    string `json:"format"`
				VideoOnly bool   `json:"videoOnly"`
				Height    int    `json:"height"`
			} `json:"videoStreams"`
		}
		if err := json.Unmarshal(body, &data); err != nil {
			errs = append(errs, inst+": bad json")
			continue
		}
		best, bestH := "", -1
		for _, s := range data.VideoStreams {
			if !strings.Contains(strings.ToUpper(s.Format), "MP4") || s.Height > height || s.Height <= bestH {
				continue
			}
			best, bestH = s.URL, s.Height
		}
		if best != "" {
			found = append(found, best)
		}
	}
	if len(found) == 0 {
		return nil, errors.New(strings.Join(errs, "; "))
	}
	return found, nil
}

// grab seeks and decodes one frame (plus an optional burst) to PNG.
func grab(ffmpeg, stream string, secs float64, out string, burst int) error {
	if err := os.MkdirAll(filepath.Dir(out), 0o755); err != nil {
		return err
	}
	ts := strconv.FormatFloat(secs, 'f', 3, 64)
	run := func(args ...string) error {
		base := []string{"-hide_banner", "-loglevel", "error", "-y",
			"-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
			"-ss", ts, "-i", stream}
		cmd := exec.Command(ffmpeg, append(base, args...)...)
		var stderr bytes.Buffer
		cmd.Stderr = &stderr
		done := make(chan error, 1)
		go func() { done <- cmd.Run() }()
		select {
		case err := <-done:
			if err != nil {
				return errors.New(strings.TrimSpace(firstLine(stderr.String())) + " (" + err.Error() + ")")
			}
			return nil
		case <-time.After(120 * time.Second):
			_ = cmd.Process.Kill()
			return errors.New("timeout after 120s")
		}
	}
	if err := run("-frames:v", "1", out); err != nil {
		return err
	}
	if burst > 0 {
		ext := filepath.Ext(out)
		pattern := strings.TrimSuffix(out, ext) + "_%03d" + ext
		if err := run("-frames:v", strconv.Itoa(burst), pattern); err != nil {
			return err
		}
	}
	return nil
}

func firstLine(s string) string {
	if i := strings.Index(s, "\n"); i >= 0 {
		return s[:i]
	}
	return s
}
