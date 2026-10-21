Add-Type -AssemblyName System.Drawing

$benchDir = "D:\teacher-research\01-frontend\ui-benchmarks"
$srcDir = "artifacts\teacher-v2\comparisons"
$outDir = "artifacts\teacher-v2\comparisons"

$pairs = @(
  @{ bench = "today-banjiopt-points.png";              src = "src-today.png";     out = "cmp-today.png";     bLabel = "基准 · 班级优化大师";  sLabel = "V2 · 今日工作台 /teacher-v2/today" },
  @{ bench = "prep-smartedu-sync-classroom.png";       src = "src-prep.png";      out = "cmp-prep.png";      bLabel = "基准 · 国家中小学智慧教育平台"; sLabel = "V2 · 备课中心 /teacher-v2/prep" },
  @{ bench = "slides-seewo-home.png";                  src = "src-slides.png";    out = "cmp-slides-seewo.png"; bLabel = "基准 · 希沃白板 5";  sLabel = "V2 · 课件工坊 /teacher-v2/slides" },
  @{ bench = "slides-kimi-ppt-help.png";               src = "src-slides.png";    out = "cmp-slides-kimi.png";  bLabel = "基准 · Kimi PPT 助手"; sLabel = "V2 · 课件工坊 /teacher-v2/slides" },
  @{ bench = "quiz-zxxk-home.png";                     src = "src-quiz.png";      out = "cmp-quiz-zxxk.png";  bLabel = "基准 · 学科网组卷";  sLabel = "V2 · 组卷中心 /teacher-v2/quiz" },
  @{ bench = "quiz-jyeoo-home.png";                    src = "src-quiz.png";      out = "cmp-quiz-jyeoo.png"; bLabel = "基准 · 菁优网";     sLabel = "V2 · 组卷中心 /teacher-v2/quiz" },
  @{ bench = "assign-wjx-exam.png";                    src = "src-assign.png";    out = "cmp-assign.png";     bLabel = "基准 · 问卷星·考试"; sLabel = "V2 · 作业与批改 /teacher-v2/assign" },
  @{ bench = "classroom-wjx-home.png";                 src = "src-classroom.png"; out = "cmp-classroom.png";  bLabel = "基准 · 问卷星";     sLabel = "V2 · 课堂互动 /teacher-v2/classroom" },
  @{ bench = "insights-jyeoo-home.png";                src = "src-insights.png";  out = "cmp-insights.png";   bLabel = "基准 · 菁优网";     sLabel = "V2 · 学情洞察 /teacher-v2/insights" },
  @{ bench = "resources-smartedu-home.png";            src = "src-resources.png"; out = "cmp-resources.png";  bLabel = "基准 · 国家中小学智慧教育平台"; sLabel = "V2 · 资源中心 /teacher-v2/resources" }
)

$targetH = 840
$headerH = 64
$divider = 6

$font = New-Object System.Drawing.Font("Microsoft YaHei", 13, [System.Drawing.FontStyle]::Bold)
$fontSmall = New-Object System.Drawing.Font("Microsoft YaHei", 10)
$grayBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(90, 98, 116))
$blueBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(29, 95, 168))
$dividerPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(208, 214, 224), $divider)

foreach ($p in $pairs) {
  $benchPath = Join-Path $benchDir $p.bench
  $srcPath = Join-Path $srcDir $p.src
  $benchImg = [System.Drawing.Image]::FromFile($benchPath)
  $srcImg = [System.Drawing.Image]::FromFile($srcPath)

  $benchW = [int][math]::Round($benchImg.Width * $targetH / $benchImg.Height)
  $srcW = [int][math]::Round($srcImg.Width * $targetH / $srcImg.Height)

  $canvasW = $benchW + $divider + $srcW
  $canvasH = $headerH + $targetH

  $bmp = New-Object System.Drawing.Bitmap($canvasW, $canvasH)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAlias
  $g.Clear([System.Drawing.Color]::FromArgb(243, 245, 248))

  $g.DrawImage($benchImg, 0, $headerH, $benchW, $targetH)
  $g.DrawImage($srcImg, $benchW + $divider, $headerH, $srcW, $targetH)

  $g.DrawString($p.bLabel, $font, $grayBrush, 16, 20)
  $g.DrawString($p.sLabel, $font, $blueBrush, $benchW + $divider + 16, 20)

  $g.DrawLine($dividerPen, $benchW + $divider / 2, 0, $benchW + $divider / 2, $canvasH)
  $g.DrawLine($dividerPen, 0, $headerH, $canvasW, $headerH)

  $outPath = Join-Path $outDir $p.out
  $bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
  Write-Host "saved $outPath ($canvasW x $canvasH)"

  $g.Dispose(); $bmp.Dispose(); $benchImg.Dispose(); $srcImg.Dispose()
}
