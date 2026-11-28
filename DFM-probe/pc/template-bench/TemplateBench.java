import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import javax.imageio.ImageIO;

/**
 * P0-1 设备端模板匹配 benchmark 原型（纯 Java，无 OpenCV）。
 * 算法与将来 probe 端一致：灰度化 → 图像金字塔（1/4 粗搜 SAD）→ 原尺寸精细验证（SAD）。
 * 用法：java TemplateBench <screen.png> <template.png> [goldX goldY]
 */
public class TemplateBench {

    static int[] toGray(BufferedImage img) {
        int w = img.getWidth(), h = img.getHeight();
        int[] g = new int[w * h];
        int[] rgb = img.getRGB(0, 0, w, h, null, 0, w);
        for (int i = 0; i < g.length; i++) {
            int p = rgb[i];
            g[i] = (int) (((p >> 16) & 0xFF) * 299 + ((p >> 8) & 0xFF) * 587 + (p & 0xFF) * 114) / 1000;
        }
        return g;
    }

    /** 金字塔层级：每层宽高减半。scale=4 表示顶层宽 = w/4。 */
    static int[][] downsample(int[] g, int w, int h, int scale) {
        int sw = w / scale, sh = h / scale;
        int[] s = new int[sw * sh];
        for (int y = 0; y < sh; y++) {
            for (int x = 0; x < sw; x++) {
                s[y * sw + x] = g[(y * scale) * w + (x * scale)];
            }
        }
        return new int[][]{s, {sw, sh}};
    }

    /** SAD（绝对差之和）均值，返回 平均差*10000 归一化分数（越小越像）。 */
    static long sadAt(int[] big, int bw, int[] tpl, int tw, int th, int ox, int oy) {
        long sum = 0;
        for (int y = 0; y < th; y++) {
            int broff = (oy + y) * bw + ox;
            int troff = y * tw;
            for (int x = 0; x < tw; x++) {
                sum += Math.abs(big[broff + x] - tpl[troff + x]);
            }
        }
        return sum / ((long) tw * th);
    }

    public static void main(String[] args) throws IOException {
        BufferedImage screenImg = ImageIO.read(new File(args[0]));
        BufferedImage tplImg = ImageIO.read(new File(args[1]));
        int[] screen = toGray(screenImg);
        int sw = screenImg.getWidth(), sh = screenImg.getHeight();
        int[] tpl = toGray(tplImg);
        int tw = tplImg.getWidth(), th = tplImg.getHeight();
        System.out.printf("screen %dx%d gray, template %dx%d gray%n", sw, sh, tw, th);

        boolean gold = args.length >= 4;
        int goldX = gold ? Integer.parseInt(args[2]) : -1;
        int goldY = gold ? Integer.parseInt(args[3]) : -1;

        long t0 = System.nanoTime();

        // ---- 金字塔粗搜：scale=4 ----
        int[][] scr4 = downsample(screen, sw, sh, 4);
        int[][] tpl4 = downsample(tpl, tw, th, 4);
        int[] s4 = scr4[0]; int cw4 = scr4[1][0], ch4 = scr4[1][1];
        int[] t4 = tpl4[0]; int tw4 = t4.length / Math.max(1, tpl4[1][1]), th4 = tpl4[1][1];
        int bw4 = cw4 - tw4 + 1, bh4 = ch4 - th4 + 1;
        if (bw4 <= 0 || bh4 <= 0) { System.out.println("template larger than screen/4"); return; }

        // 记录候选 top-N（多尺度容错：粗搜分数阈值放宽，保留前 8 名）
        long best = Long.MAX_VALUE; int bx = -1, by = -1;
        long[] candScore = new long[8]; int[] candX = new int[8]; int[] candY = new int[8]; int candN = 0;
        for (int y = 0; y < bh4; y++) {
            for (int x = 0; x < bw4; x++) {
                long s = sadAt(s4, cw4, t4, tw4, th4, x, y);
                if (s < best) { best = s; bx = x; by = y; }
                // 插入候选列表（简单选择）
                if (candN < 8) {
                    candScore[candN] = s; candX[candN] = x; candY[candN] = y; candN++;
                } else {
                    int worst = 0;
                    for (int i = 1; i < 8; i++) if (candScore[i] > candScore[worst]) worst = i;
                    if (s < candScore[worst]) { candScore[worst] = s; candX[worst] = x; candY[worst] = y; }
                }
            }
        }
        long t1 = System.nanoTime();
        double coarseMs = (t1 - t0) / 1e6;
        System.out.printf("coarse(scale=4): %dx%d positions, best=%d @(%d,%d), %.1f ms%n", bw4, bh4, best, bx, by, coarseMs);

        // ---- 原尺寸精细验证：候选 8 个，每候选在 ±3 像素窗口内搜最优 ----
        double fineBest = Double.MAX_VALUE; int fx = -1, fy = -1;
        int win = 3;
        for (int c = 0; c < candN; c++) {
            int cx0 = candX[c] * 4, cy0 = candY[c] * 4;
            for (int dy = -win; dy <= win; dy++) {
                for (int dx = -win; dx <= win; dx++) {
                    int ox = cx0 + dx, oy = cy0 + dy;
                    if (ox < 0 || oy < 0 || ox + tw > sw || oy + th > sh) continue;
                    long s = sadAt(screen, sw, tpl, tw, th, ox, oy);
                    double norm = s / 10000.0;
                    if (norm < fineBest) { fineBest = norm; fx = ox; fy = oy; }
                }
            }
        }
        long t2 = System.nanoTime();
        double fineMs = (t2 - t1) / 1e6;

        int positions = (2 * win + 1) * (2 * win + 1) * candN;
        System.out.printf("fine(full-res): %d positions, best=%.1f/10k @(%d,%d), %.1f ms%n", positions, fineBest, fx, fy, fineMs);
        System.out.printf("TOTAL %.1f ms%n", (t2 - t0) / 1e6);

        if (gold) {
            int hit = (Math.abs(fx - goldX) <= 4 && Math.abs(fy - goldY) <= 4) ? 1 : 0;
            System.out.printf("GOLD check: expect(%d,%d) got(%d,%d) -> %s%n", goldX, goldY, fx, fy, hit == 1 ? "HIT" : "MISS");
        }
    }
}
