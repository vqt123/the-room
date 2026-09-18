// hideit: hide one thing inside a busy picture, and say exactly where it went.
//
// This is the private half of "Find It". A public model can draw a crowded
// scene and it can draw a product, but it cannot tell you where it put
// anything, so a model alone cannot score a click. This program does the
// placing, so it owns the answer: it cuts the product off its background,
// measures where the scene is most cluttered, drops the product into the
// clutter at a matched size and tint, and prints the exact box.
//
// The box is printed on stdout and never drawn. Whoever runs this holds the
// answer key; the person looking at the picture does not.
package main

import (
	"flag"
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"math"
	"math/rand"
	"os"
	"sort"
	"time"
)

func fail(msg string) {
	fmt.Fprintln(os.Stderr, "hideit: "+msg)
	os.Exit(1)
}

func main() {
	scenePath := flag.String("scene", "", "the busy picture (PNG)")
	prodPath := flag.String("product", "", "the thing to hide (PNG)")
	out := flag.String("out", "puzzle.png", "output PNG")
	difficulty := flag.Int("difficulty", 3, "1 easy (big, plain surroundings) to 5 cruel (small, deepest clutter)")
	seed := flag.Int64("seed", 0, "0 = time")
	keepAlpha := flag.Bool("keep-alpha", false, "trust the product's own alpha instead of cutting its background")
	flag.Parse()

	if *scenePath == "" || *prodPath == "" {
		fail("need -scene and -product")
	}
	if *difficulty < 1 {
		*difficulty = 1
	}
	if *difficulty > 5 {
		*difficulty = 5
	}
	if *seed == 0 {
		*seed = time.Now().UnixNano()
	}
	rng := rand.New(rand.NewSource(*seed))

	scene, err := loadPNG(*scenePath)
	if err != nil {
		fail("scene: " + err.Error())
	}
	prod, err := loadPNG(*prodPath)
	if err != nil {
		fail("product: " + err.Error())
	}

	if !*keepAlpha {
		cutBackground(prod)
	}
	prod = trimToContent(prod)
	if prod.Bounds().Dx() < 4 || prod.Bounds().Dy() < 4 {
		fail("nothing left of the product after cutting its background")
	}

	sw, sh := scene.Bounds().Dx(), scene.Bounds().Dy()
	short := sw
	if sh < short {
		short = sh
	}
	// How big the thing is, as a share of the short edge. Easy leaves it obvious.
	fracs := map[int]float64{1: 0.130, 2: 0.095, 3: 0.070, 4: 0.052, 5: 0.038}
	target := fracs[*difficulty] * float64(short)
	pw, ph := prod.Bounds().Dx(), prod.Bounds().Dy()
	scale := target / math.Max(float64(pw), float64(ph))
	nw, nh := int(float64(pw)*scale+0.5), int(float64(ph)*scale+0.5)
	if nw < 8 {
		nw = 8
	}
	if nh < 8 {
		nh = 8
	}
	small := resize(prod, nw, nh)

	x, y := choosePlacement(scene, small, *difficulty, rng)

	// Nudge the thing towards the local colour so it sits in the picture rather
	// than on top of it. Easy modes barely touch it.
	blend := []float64{0.10, 0.16, 0.22, 0.28, 0.34}[*difficulty-1]
	tintToSurroundings(small, scene, x, y, blend)

	puzzle := image.NewNRGBA(scene.Bounds())
	draw.Draw(puzzle, puzzle.Bounds(), scene, scene.Bounds().Min, draw.Src)
	draw.Draw(puzzle, image.Rect(x, y, x+nw, y+nh), small, image.Point{}, draw.Over)

	if err := writePNG(*out, puzzle); err != nil {
		fail(err.Error())
	}

	// Per mille of the picture, so a browser can score a click on any display size.
	px := int(math.Round(float64(x) * 1000 / float64(sw)))
	py := int(math.Round(float64(y) * 1000 / float64(sh)))
	pwm := int(math.Round(float64(nw) * 1000 / float64(sw)))
	phm := int(math.Round(float64(nh) * 1000 / float64(sh)))
	fmt.Printf("key=hideit-x%d-y%d-w%d-h%d\n", px, py, pwm, phm)
	fmt.Printf("pixels=%d,%d,%d,%d scene=%dx%d\n", x, y, nw, nh, sw, sh)
}

func loadPNG(p string) (*image.NRGBA, error) {
	f, err := os.Open(p)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	src, err := png.Decode(f)
	if err != nil {
		return nil, err
	}
	b := src.Bounds()
	dst := image.NewNRGBA(image.Rect(0, 0, b.Dx(), b.Dy()))
	draw.Draw(dst, dst.Bounds(), src, b.Min, draw.Src)
	return dst, nil
}

func writePNG(p string, img image.Image) error {
	f, err := os.Create(p)
	if err != nil {
		return err
	}
	defer f.Close()
	return png.Encode(f, img)
}

// cutBackground clears the flat border a product shot usually sits on: flood
// fill inward from every edge pixel whose colour still matches the corners.
func cutBackground(img *image.NRGBA) {
	b := img.Bounds()
	w, h := b.Dx(), b.Dy()
	corners := []color.NRGBA{
		img.NRGBAAt(0, 0), img.NRGBAAt(w-1, 0), img.NRGBAAt(0, h-1), img.NRGBAAt(w-1, h-1),
	}
	// Only cut when the corners agree; otherwise the picture has no flat border
	// and cutting would eat the subject.
	for i := 1; i < len(corners); i++ {
		if colourDist(corners[0], corners[i]) > 60 {
			return
		}
	}
	bg := corners[0]
	const tol = 52.0
	visited := make([]bool, w*h)
	queue := make([]int, 0, w*h/4)
	push := func(x, y int) {
		if x < 0 || y < 0 || x >= w || y >= h {
			return
		}
		i := y*w + x
		if visited[i] {
			return
		}
		if colourDist(img.NRGBAAt(x, y), bg) > tol {
			return
		}
		visited[i] = true
		queue = append(queue, i)
	}
	for x := 0; x < w; x++ {
		push(x, 0)
		push(x, h-1)
	}
	for y := 0; y < h; y++ {
		push(0, y)
		push(w-1, y)
	}
	for n := 0; n < len(queue); n++ {
		i := queue[n]
		x, y := i%w, i/w
		push(x+1, y)
		push(x-1, y)
		push(x, y+1)
		push(x, y-1)
	}
	for i, v := range visited {
		if v {
			x, y := i%w, i/w
			c := img.NRGBAAt(x, y)
			c.A = 0
			img.SetNRGBA(x, y, c)
		}
	}
	featherEdges(img)
}

// featherEdges softens the one-pixel staircase the flood fill leaves behind.
func featherEdges(img *image.NRGBA) {
	b := img.Bounds()
	w, h := b.Dx(), b.Dy()
	orig := make([]uint8, w*h)
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			orig[y*w+x] = img.NRGBAAt(x, y).A
		}
	}
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			sum, n := 0, 0
			for dy := -1; dy <= 1; dy++ {
				for dx := -1; dx <= 1; dx++ {
					nx, ny := x+dx, y+dy
					if nx < 0 || ny < 0 || nx >= w || ny >= h {
						continue
					}
					sum += int(orig[ny*w+nx])
					n++
				}
			}
			c := img.NRGBAAt(x, y)
			c.A = uint8(sum / n)
			img.SetNRGBA(x, y, c)
		}
	}
}

// trimToContent crops away fully transparent margins.
func trimToContent(img *image.NRGBA) *image.NRGBA {
	b := img.Bounds()
	w, h := b.Dx(), b.Dy()
	minX, minY, maxX, maxY := w, h, -1, -1
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			if img.NRGBAAt(x, y).A > 12 {
				if x < minX {
					minX = x
				}
				if y < minY {
					minY = y
				}
				if x > maxX {
					maxX = x
				}
				if y > maxY {
					maxY = y
				}
			}
		}
	}
	if maxX < minX || maxY < minY {
		return img
	}
	out := image.NewNRGBA(image.Rect(0, 0, maxX-minX+1, maxY-minY+1))
	draw.Draw(out, out.Bounds(), img, image.Point{X: minX, Y: minY}, draw.Src)
	return out
}

func colourDist(a, b color.NRGBA) float64 {
	dr := float64(a.R) - float64(b.R)
	dg := float64(a.G) - float64(b.G)
	db := float64(a.B) - float64(b.B)
	return math.Sqrt(dr*dr + dg*dg + db*db)
}

func resize(src *image.NRGBA, nw, nh int) *image.NRGBA {
	b := src.Bounds()
	w, h := b.Dx(), b.Dy()
	dst := image.NewNRGBA(image.Rect(0, 0, nw, nh))
	for y := 0; y < nh; y++ {
		fy := (float64(y) + 0.5) * float64(h) / float64(nh)
		y0 := int(fy)
		if y0 >= h {
			y0 = h - 1
		}
		for x := 0; x < nw; x++ {
			fx := (float64(x) + 0.5) * float64(w) / float64(nw)
			x0 := int(fx)
			if x0 >= w {
				x0 = w - 1
			}
			// Average the source block so shrinking keeps the shape readable.
			x1 := int((float64(x)+1.5)*float64(w)/float64(nw)) + 1
			y1 := int((float64(y)+1.5)*float64(h)/float64(nh)) + 1
			if x1 > w {
				x1 = w
			}
			if y1 > h {
				y1 = h
			}
			var r, g, bl, a, n float64
			for sy := y0; sy < y1; sy++ {
				for sx := x0; sx < x1; sx++ {
					c := src.NRGBAAt(sx, sy)
					af := float64(c.A) / 255
					r += float64(c.R) * af
					g += float64(c.G) * af
					bl += float64(c.B) * af
					a += float64(c.A)
					n++
				}
			}
			if n == 0 {
				continue
			}
			aa := a / n
			if aa < 1 {
				dst.SetNRGBA(x, y, color.NRGBA{})
				continue
			}
			wsum := a / 255
			dst.SetNRGBA(x, y, color.NRGBA{
				R: clamp8(r / wsum), G: clamp8(g / wsum), B: clamp8(bl / wsum), A: clamp8(aa),
			})
		}
	}
	return dst
}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func clamp8(v float64) uint8 {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return uint8(v + 0.5)
}

// choosePlacement puts the thing where the picture is already busiest, because
// clutter is what hides an object. It scores every candidate box on how much
// detail surrounds it and how close the local colour is to the object's own.
func choosePlacement(scene, prod *image.NRGBA, difficulty int, rng *rand.Rand) (int, int) {
	sb := scene.Bounds()
	sw, sh := sb.Dx(), sb.Dy()
	pw, ph := prod.Bounds().Dx(), prod.Bounds().Dy()

	// Detail: per-pixel gradient, then a summed-area table so any box is O(1).
	grad := make([]float64, sw*sh)
	lum := func(x, y int) float64 {
		c := scene.NRGBAAt(x, y)
		return 0.299*float64(c.R) + 0.587*float64(c.G) + 0.114*float64(c.B)
	}
	for y := 0; y < sh; y++ {
		for x := 0; x < sw; x++ {
			x1, y1 := x+1, y+1
			if x1 >= sw {
				x1 = sw - 1
			}
			if y1 >= sh {
				y1 = sh - 1
			}
			gx := lum(x1, y) - lum(x, y)
			gy := lum(x, y1) - lum(x, y)
			grad[y*sw+x] = math.Abs(gx) + math.Abs(gy)
		}
	}
	sat := integral(grad, sw, sh)
	boxSum := func(x0, y0, x1, y1 int) float64 {
		return sat[y1*(sw+1)+x1] - sat[y0*(sw+1)+x1] - sat[y1*(sw+1)+x0] + sat[y0*(sw+1)+x0]
	}

	pr, pg, pb := meanColour(prod)
	margin := int(0.04 * float64(sw))
	type cand struct {
		x, y  int
		score float64
	}
	var best []cand
	stride := 6
	for y := margin; y+ph < sh-margin; y += stride {
		for x := margin; x+pw < sw-margin; x += stride {
			detail := boxSum(x, y, x+pw, y+ph) / float64(pw*ph)
			lr, lg, lb := regionMean(scene, x, y, pw, ph)
			near := colourDist(color.NRGBA{R: clamp8(pr), G: clamp8(pg), B: clamp8(pb)},
				color.NRGBA{R: clamp8(lr), G: clamp8(lg), B: clamp8(lb)})
			// Busy is good. Similar local colour is good. Dead centre is too kind.
			cx, cy := float64(x+pw/2), float64(y+ph/2)
			centre := 1 - math.Hypot(cx-float64(sw)/2, cy-float64(sh)/2)/(float64(sw)/2)
			score := detail*2.2 - near*0.20 - math.Max(centre, 0)*14
			best = append(best, cand{x, y, score})
		}
	}
	if len(best) == 0 {
		return (sw - pw) / 2, (sh - ph) / 2
	}
	sort.Slice(best, func(i, j int) bool { return best[i].score > best[j].score })

	// The best few positions are all inside whichever blob is busiest, so picking
	// among them hides the thing in the same place every time. Keep only
	// candidates that are far apart, so the pool is a set of different hiding
	// places rather than one place sampled repeatedly.
	sep := float64(pw+ph) * 0.9
	var spread []cand
	for _, c := range best {
		ok := true
		for _, s := range spread {
			if math.Hypot(float64(c.x-s.x), float64(c.y-s.y)) < sep {
				ok = false
				break
			}
		}
		if ok {
			spread = append(spread, c)
		}
		if len(spread) >= 10 {
			break
		}
	}
	if len(spread) == 0 {
		spread = best[:1]
	}
	// Weight the choice towards the better hiding places without ever being
	// certain: first place twice as likely as last.
	idx := 0
	if len(spread) > 1 {
		w := make([]float64, len(spread))
		var total float64
		for i := range w {
			w[i] = 2 - float64(i)/float64(len(spread)-1)
			total += w[i]
		}
		r := rng.Float64() * total
		for i, v := range w {
			r -= v
			if r <= 0 {
				idx = i
				break
			}
		}
	}
	c := spread[idx]
	// Come off the search stride so the box is not always on a multiple of it.
	c.x += rng.Intn(stride) - stride/2
	c.y += rng.Intn(stride) - stride/2
	c.x = clampInt(c.x, margin, sw-pw-margin)
	c.y = clampInt(c.y, margin, sh-ph-margin)
	return c.x, c.y
}

func integral(v []float64, w, h int) []float64 {
	sat := make([]float64, (w+1)*(h+1))
	for y := 0; y < h; y++ {
		var rowsum float64
		for x := 0; x < w; x++ {
			rowsum += v[y*w+x]
			sat[(y+1)*(w+1)+x+1] = sat[y*(w+1)+x+1] + rowsum
		}
	}
	return sat
}

func meanColour(img *image.NRGBA) (float64, float64, float64) {
	b := img.Bounds()
	var r, g, bl, n float64
	for y := 0; y < b.Dy(); y++ {
		for x := 0; x < b.Dx(); x++ {
			c := img.NRGBAAt(x, y)
			if c.A < 40 {
				continue
			}
			r += float64(c.R)
			g += float64(c.G)
			bl += float64(c.B)
			n++
		}
	}
	if n == 0 {
		return 128, 128, 128
	}
	return r / n, g / n, bl / n
}

func regionMean(img *image.NRGBA, x, y, w, h int) (float64, float64, float64) {
	var r, g, bl, n float64
	for yy := y; yy < y+h; yy += 2 {
		for xx := x; xx < x+w; xx += 2 {
			c := img.NRGBAAt(xx, yy)
			r += float64(c.R)
			g += float64(c.G)
			bl += float64(c.B)
			n++
		}
	}
	if n == 0 {
		return 128, 128, 128
	}
	return r / n, g / n, bl / n
}

// tintToSurroundings pulls the object part of the way towards the colours it is
// about to sit in, which is what stops a pasted thing from glowing.
func tintToSurroundings(prod, scene *image.NRGBA, x, y int, amount float64) {
	pw, ph := prod.Bounds().Dx(), prod.Bounds().Dy()
	lr, lg, lb := regionMean(scene, x, y, pw, ph)
	for yy := 0; yy < ph; yy++ {
		for xx := 0; xx < pw; xx++ {
			c := prod.NRGBAAt(xx, yy)
			if c.A == 0 {
				continue
			}
			c.R = clamp8(float64(c.R)*(1-amount) + lr*amount)
			c.G = clamp8(float64(c.G)*(1-amount) + lg*amount)
			c.B = clamp8(float64(c.B)*(1-amount) + lb*amount)
			prod.SetNRGBA(xx, yy, c)
		}
	}
}
