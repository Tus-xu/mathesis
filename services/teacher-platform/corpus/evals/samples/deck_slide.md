# deck/slide.j2 实跑样本（derivation 页）

```json
{
  "page_type": "derivation",
  "title": "椭圆标准方程的推导",
  "points": [
    "以焦点F₁、F₂所在直线为x轴，线段F₁F₂中垂线为y轴建立坐标系。",
    "设焦距|F₁F₂|=2c，动点P(x,y)到两焦点距离和为2a，且2a>2c。",
    "根据椭圆定义列出方程：√[(x+c)²+y²]+√[(x-c)²+y²]=2a。",
    "移项、平方、整理，消去根号，得到(a²-c²)x²+a²y²=a²(a²-c²)。",
    "令b²=a²-c²（a>b>0），代入化简得标准方程：x²/a²+y²/b²=1。"
  ],
  "formula": "\\frac{x^2}{a^2}+\\frac{y^2}{b^2}=1\\quad(a>b>0,\\;b^2=a^2-c^2)",
  "visual": {
    "kind": "preset",
    "preset": "ellipse_with_foci",
    "params": {
      "show_foci": true,
      "show_axes": true,
      "show_center": true,
      "show_major_minor_axis": false,
      "label_foci": "F_1, F_2",
      "label_center": "O",
      "a_value": 5,
      "b_value": 3,
      "c_value": 4
    },
    "boardJson": null
  },
  "teacher_note": "推导过程中，平方消根号是难点，可板书展示关键步骤：第一次平方后得 (x+c)²+y²=4a²-4a√[(x-c)²+y²]+(x-c)²+y²，整理得 a√[(x-c)²+y²]=a²-cx。强调令 b²=a²-c² 的几何意义（半短轴长）。",
  "missing_evidence": []
}
```
