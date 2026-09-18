// vhstape: turn a handful of stills into one VHS camcorder tape.
//
// This is the private part of "Senior Year '88". The models make the pictures;
// this program makes them a tape: it crops each still to 4:3, gives it a slow
// camcorder drift, bleeds the chroma sideways, adds tape grain, scanlines, a
// rolling tracking band and head-switching noise at the bottom edge, burns an
// advancing date stamp in a hand-drawn bitmap font, cuts between scenes on a
// frame of snow, and lays hiss and mains hum under the whole thing.
//
// Everything is ffmpeg filter work and hand-drawn overlays; no model does any
// of it. It carries its own ffmpeg (bin/ffmpeg next to the executable) and
// falls back to whatever ffmpeg is on PATH.
package main

import (
	"bytes"
	"errors"
	"flag"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func fail(msg string) {
	fmt.Fprintln(os.Stderr, "vhstape: "+msg)
	os.Exit(1)
}

func main() {
	frames := flag.String("frames", "", "comma-separated PNG paths, one per scene, in order")
	stamps := flag.String("stamps", "", "pipe-separated date stamps, one per scene (e.g. \"SEP 04 1987|MAY 20 1988\")")
	out := flag.String("out", "tape.mp4", "output mp4")
	label := flag.String("label", "", "text burned top-left on the first scene (e.g. \"SENIOR YEAR '88\")")
	secs := flag.Float64("seconds", 2.6, "seconds per scene")
	fps := flag.Int("fps", 24, "frames per second")
	w := flag.Int("w", 720, "tape width (4:3 is the period-correct shape)")
	h := flag.Int("h", 540, "tape height")
	portrait := flag.Bool("portrait", false, "pad the finished tape into a 1080x1920 phone frame")
	seed := flag.Int64("seed", 0, "seed for the wobble and grain (0 = time)")
	keep := flag.Bool("keep", false, "keep the working directory (debugging)")
	flag.Parse()

	if *frames == "" {
		fail("missing -frames")
	}
	paths := strings.Split(*frames, ",")
	for i := range paths {
		paths[i] = strings.TrimSpace(paths[i])
		if paths[i] == "" {
			fail("empty path in -frames")
		}
		if _, err := os.Stat(paths[i]); err != nil {
			fail("frame not readable: " + paths[i])
		}
	}
	var marks []string
	if *stamps != "" {
		for _, s := range strings.Split(*stamps, "|") {
			marks = append(marks, strings.ToUpper(strings.TrimSpace(s)))
		}
	}
	if *seed == 0 {
		*seed = time.Now().UnixNano()
	}
	rng := rand.New(rand.NewSource(*seed))

	ff := ffmpegPath()
	if ff == "" {
		fail("no ffmpeg: not next to the executable and not on PATH")
	}

	work, err := os.MkdirTemp("", "vhstape")
	if err != nil {
		fail(err.Error())
	}
	if !*keep {
		defer os.RemoveAll(work)
	}

	// Overlays drawn here, not by ffmpeg: one scanline sheet for every scene,
	// one stamp sheet per scene.
	lines := filepath.Join(work, "scanlines.png")
	if err := writeScanlines(lines, *w, *h); err != nil {
		fail("scanlines: " + err.Error())
	}
	overlays := make([]string, len(paths))
	for i := range paths {
		stamp := ""
		if i < len(marks) {
			stamp = marks[i]
		}
		top := ""
		if i == 0 {
			top = *label
		}
		p := filepath.Join(work, fmt.Sprintf("ov_%02d.png", i))
		if err := writeOverlay(p, *w, *h, stamp, top, i == 0); err != nil {
			fail("overlay: " + err.Error())
		}
		overlays[i] = p
	}

	scenes := make([]string, 0, len(paths)*2)
	for i, src := range paths {
		dst := filepath.Join(work, fmt.Sprintf("scene_%02d.mp4", i))
		if err := renderScene(ff, src, overlays[i], lines, dst, *w, *h, *fps, *secs, rng); err != nil {
			fail(fmt.Sprintf("scene %d: %v", i+1, err))
		}
		if i > 0 {
			g := filepath.Join(work, fmt.Sprintf("glitch_%02d.mp4", i))
			if err := renderGlitch(ff, g, *w, *h, *fps); err != nil {
				fail(fmt.Sprintf("glitch %d: %v", i, err))
			}
			scenes = append(scenes, g)
		}
		scenes = append(scenes, dst)
	}

	list := filepath.Join(work, "list.txt")
	var b bytes.Buffer
	for _, s := range scenes {
		fmt.Fprintf(&b, "file '%s'\n", s)
	}
	if err := os.WriteFile(list, b.Bytes(), 0o644); err != nil {
		fail(err.Error())
	}

	if err := finish(ff, list, *out, *w, *h, *fps, *portrait); err != nil {
		fail("final pass: " + err.Error())
	}
	fmt.Println(*out)
}

// ffmpegPath prefers the copy shipped beside this executable, so the tape looks
// the same wherever it runs, and falls back to the image's own ffmpeg.
func ffmpegPath() string {
	here, err := os.Executable()
	if err == nil {
		p := filepath.Join(filepath.Dir(here), "ffmpeg")
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			if runnable(p) {
				return p
			}
		}
	}
	if p, err := exec.LookPath("ffmpeg"); err == nil {
		return p
	}
	return ""
}

func runnable(p string) bool {
	cmd := exec.Command(p, "-hide_banner", "-version")
	return cmd.Run() == nil
}

func run(ff string, args ...string) error {
	cmd := exec.Command(ff, args...)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	done := make(chan error, 1)
	go func() { done <- cmd.Run() }()
	select {
	case err := <-done:
		if err != nil {
			return errors.New(lastLines(stderr.String(), 3))
		}
		return nil
	case <-time.After(180 * time.Second):
		_ = cmd.Process.Kill()
		return errors.New("ffmpeg timeout after 180s")
	}
}

func lastLines(s string, n int) string {
	parts := strings.Split(strings.TrimSpace(s), "\n")
	if len(parts) > n {
		parts = parts[len(parts)-n:]
	}
	return strings.TrimSpace(strings.Join(parts, " | "))
}

// renderScene: one still becomes one shot of the tape.
func renderScene(ff, src, overlay, lines, dst string, w, h, fps int, secs float64, rng *rand.Rand) error {
	total := int(secs * float64(fps))
	if total < 2 {
		total = 2
	}
	// Oversample so the drift has pixels to move through, and keep it even.
	sw, sh := even(int(float64(w)*1.45)), even(int(float64(h)*1.45))
	zmax := 1.10 + rng.Float64()*0.06
	zrate := (zmax - 1.0) / float64(total)
	px := 10 + rng.Float64()*14
	py := 6 + rng.Float64()*10
	phase := rng.Float64() * 6.28
	if rng.Intn(2) == 0 { // half the shots drift the other way
		px = -px
	}
	wob := 6 // horizontal wobble margin, in pixels
	band := rng.Float64() * float64(h)

	fc := fmt.Sprintf(
		"[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,"+
			"zoompan=z='min(1.001+%.6f*on,%.4f)':x='iw/2-(iw/zoom/2)+%.3f*sin(on/%d*0.5+%.3f)':y='ih/2-(ih/zoom/2)+%.3f*sin(on/%d*0.37)':d=%d:s=%dx%d:fps=%d,"+
			"chromashift=cbh=4:crh=-5:cbv=1,"+
			"crop=%d:%d:x='%d+%d*sin(t*7.3)':y=0,scale=%d:%d,"+
			"eq=saturation=0.80:contrast=0.97:brightness=0.022:gamma=1.05,"+
			"colorbalance=rs=0.05:bs=-0.05:rm=0.06:gm=0.01:bm=-0.06:rh=0.02:bh=-0.03,"+
			"noise=alls=11:allf=t+u,"+
			"gblur=sigma=0.35[v];"+
			"[v][1:v]overlay=0:0[vl];"+
			"[vl][2:v]overlay=0:0[vo];"+
			"[3:v]noise=alls=95:allf=t+u,format=yuva420p,colorchannelmixer=aa=0.5[hs];"+
			"[vo][hs]overlay=0:%d[vh];"+
			"[vh]drawbox=x=0:y='mod(t*230+%.1f\\,%d)-14':w=iw:h=7:color=white@0.09:t=fill,"+
			"drawbox=x=0:y='mod(t*230+%.1f\\,%d)-6':w=iw:h=2:color=white@0.05:t=fill,"+
			"format=yuv420p[out]",
		sw, sh, sw, sh,
		zrate, zmax, px, fps, phase, py, fps, total, w, h, fps,
		w-2*wob, h, wob, wob-2, w, h,
		h-12,
		band, h+28, band, h+28,
	)

	return run(ff,
		"-hide_banner", "-loglevel", "error", "-y",
		"-loop", "1", "-framerate", fmt.Sprint(fps), "-i", src,
		"-loop", "1", "-framerate", fmt.Sprint(fps), "-i", lines,
		"-loop", "1", "-framerate", fmt.Sprint(fps), "-i", overlay,
		"-f", "lavfi", "-i", fmt.Sprintf("color=gray:s=%dx12:r=%d", w, fps),
		"-filter_complex", fc,
		"-map", "[out]", "-frames:v", fmt.Sprint(total),
		"-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
		"-r", fmt.Sprint(fps), dst)
}

// renderGlitch: the frames of snow between two shots, where the tape was stopped.
func renderGlitch(ff, dst string, w, h, fps int) error {
	n := fps / 8
	if n < 2 {
		n = 2
	}
	return run(ff,
		"-hide_banner", "-loglevel", "error", "-y",
		"-f", "lavfi", "-i", fmt.Sprintf("color=gray:s=%dx%d:r=%d", w, h, fps),
		"-filter_complex", "[0:v]noise=alls=100:allf=t+u,eq=contrast=1.5,format=yuv420p[out]",
		"-map", "[out]", "-frames:v", fmt.Sprint(n),
		"-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
		"-r", fmt.Sprint(fps), dst)
}

// finish: join the shots and lay the tape's own sound under them.
func finish(ff, list, out string, w, h, fps int, portrait bool) error {
	vf := "format=yuv420p"
	if portrait {
		vf = fmt.Sprintf("scale=1080:-2,pad=1080:1920:0:(1920-ih)/2:color=black,format=yuv420p")
	}
	return run(ff,
		"-hide_banner", "-loglevel", "error", "-y",
		"-f", "concat", "-safe", "0", "-i", list,
		"-f", "lavfi", "-i", "anoisesrc=color=brown:amplitude=0.06:r=44100",
		"-f", "lavfi", "-i", "sine=frequency=60:sample_rate=44100",
		"-filter_complex",
		"[0:v]"+vf+"[v];"+
			"[1:a][2:a]amix=inputs=2:weights='1 0.35':duration=shortest,highpass=f=70,lowpass=f=6200,volume=0.45[a]",
		"-map", "[v]", "-map", "[a]", "-shortest",
		"-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
		"-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
		"-r", fmt.Sprint(fps), out)
}

func even(n int) int {
	if n%2 != 0 {
		return n + 1
	}
	return n
}

// writeScanlines draws the line sheet every scene is laid under.
func writeScanlines(path string, w, h int) error {
	img := image.NewNRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		var a uint8
		switch y % 3 {
		case 0:
			a = 48
		case 1:
			a = 12
		}
		if a == 0 {
			continue
		}
		for x := 0; x < w; x++ {
			img.SetNRGBA(x, y, color.NRGBA{0, 0, 0, a})
		}
	}
	return writePNG(path, img)
}

// writeOverlay draws the date stamp (bottom right, the way a camcorder burned
// it in) and, on the first scene, the PLAY marker and the tape's label.
func writeOverlay(path string, w, h int, stamp, label string, first bool) error {
	img := image.NewNRGBA(image.Rect(0, 0, w, h))
	amber := color.NRGBA{255, 214, 138, 235}
	white := color.NRGBA{236, 236, 236, 225}

	if stamp != "" {
		scale := w / 150
		if scale < 3 {
			scale = 3
		}
		tw := textWidth(stamp, scale)
		x := w - tw - 7*scale
		y := h - 7*scale - 6*scale
		drawText(img, stamp, x, y, scale, amber, true)
	}
	if first {
		scale := w / 180
		if scale < 2 {
			scale = 2
		}
		drawText(img, "PLAY", 7*scale, 5*scale, scale, white, true)
		// The two triangles a deck drew next to PLAY.
		bx := 7*scale + textWidth("PLAY", scale) + 3*scale
		by := 5 * scale
		for i := 0; i < 7*scale; i++ {
			run := i
			if i > 3*scale {
				run = 7*scale - i
			}
			for j := 0; j < run; j++ {
				setPix(img, bx+j, by+i, white)
			}
		}
	}
	if label != "" {
		scale := w / 200
		if scale < 2 {
			scale = 2
		}
		drawText(img, label, 7*scale, h-7*scale-9*scale, scale, white, true)
	}
	return writePNG(path, img)
}

func drawText(img *image.NRGBA, s string, x, y, scale int, c color.NRGBA, shadow bool) {
	if shadow {
		drawTextRaw(img, s, x+scale, y+scale, scale, color.NRGBA{0, 0, 0, 170})
	}
	drawTextRaw(img, s, x, y, scale, c)
}

func drawTextRaw(img *image.NRGBA, s string, x, y, scale int, c color.NRGBA) {
	cx := x
	for _, r := range s {
		g, ok := glyphs[r]
		if !ok {
			g = glyphs[' ']
		}
		for row := 0; row < 7; row++ {
			bits := g[row]
			for col := 0; col < 5; col++ {
				if bits&(1<<(4-col)) == 0 {
					continue
				}
				for dy := 0; dy < scale; dy++ {
					for dx := 0; dx < scale; dx++ {
						setPix(img, cx+col*scale+dx, y+row*scale+dy, c)
					}
				}
			}
		}
		cx += 6 * scale
	}
}

func setPix(img *image.NRGBA, x, y int, c color.NRGBA) {
	b := img.Bounds()
	if x < b.Min.X || y < b.Min.Y || x >= b.Max.X || y >= b.Max.Y {
		return
	}
	img.SetNRGBA(x, y, c)
}

func writePNG(path string, img image.Image) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return png.Encode(f, img)
}
