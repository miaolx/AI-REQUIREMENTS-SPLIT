from qoder_analyzer import _extract_qoder_json as f

case1 = '{"changes": []}'
case2 = '前言文字\n```json\n{"changes": [{"path": "a.py", "summary": "x", "min_lines": 1, "max_lines": 2, "is_new": false},]}\n```\n结尾'
case3 = '分析完成，结果如下：{"changes":[{"path":"b.py","summary":"y","min_lines":2,"max_lines":5,"is_new":true}]} 以上。'

print(f(case1))
print(f(case2))
print(f(case3))
try:
    f("没有任何 JSON 的计划文本")
except RuntimeError as exc:
    print("expected error:", exc)

from app import app
print("app routes:", sorted(app.openapi()["paths"]))
