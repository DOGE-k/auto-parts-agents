"""测试 AIP 端点。"""
import urllib.request
import json

# 测试 AIP 总览
url = "http://127.0.0.1:8001/aip/overview"
try:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print("✅ AIP 总览: 共", len(data["agents"]), "个智能体")
        for agent in data["agents"]:
            skills = len(agent["skills"])
            print(f"   - {agent['name']} ({agent['type']}): {skills} 技能, AIC={agent['aic']}")
except Exception as e:
    print("❌ AIP 总览:", e)

# 测试健康检查
for agent_path in ["quotation", "procurement", "tracking", "quality-document"]:
    url = f"http://127.0.0.1:8001/aip/{agent_path}/health"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"✅ {agent_path} health: {data.get('status', 'unknown')}")
    except Exception as e:
        print(f"❌ {agent_path} health: {e}")
