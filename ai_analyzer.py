"""DeepSeek AI batch analysis for Ozon product selection."""

import json
from openai import OpenAI, APIError, APIConnectionError, RateLimitError

AI_FIELDS = [
    "SkuId", "一级类目", "三级类目", "绿标价", "月销量",
    "毛利率", "利润(预)", "跟卖数量", "展示至下单转化率", "阿里巴巴采购价(预)",
]


def prepare_ai_payload(df, max_products=20):
    top = df.head(max_products)
    available = [f for f in AI_FIELDS if f in top.columns]
    subset = top[available].copy()
    for col in subset.select_dtypes(include=["float64"]).columns:
        subset[col] = subset[col].round(2)
    records = subset.fillna("N/A").to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False, indent=2)


def generate_report(config: dict, payload: str) -> dict:
    ai_cfg = config.get("ai", {})
    api_key = ai_cfg.get("api_key", "")
    if not api_key or api_key == "sk-YOUR-DEEPSEEK-KEY":
        return {"error": "请先在 config.yaml 中配置 DeepSeek API Key"}

    prompt_template = ai_cfg.get("prompt_template", "分析以下商品数据：\n{data}")
    prompt = prompt_template.format(data=payload)

    try:
        client = OpenAI(
            api_key=api_key,
            base_url=ai_cfg.get("base_url", "https://api.deepseek.com"),
        )
        response = client.chat.completions.create(
            model=ai_cfg.get("model", "deepseek-chat"),
            messages=[
                {"role": "system", "content": "你是跨境电商选品专家，擅长 Ozon 平台数据分析。请用 Markdown 格式输出分析报告。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2000,
            temperature=0.7,
        )
        report = response.choices[0].message.content
        return {"report": report}

    except APIConnectionError:
        return {"error": "无法连接 DeepSeek API，请检查网络或代理设置"}
    except RateLimitError:
        return {"error": "API 调用频率超限，请稍后重试"}
    except APIError as e:
        return {"error": f"DeepSeek API 错误: {e.message}"}
    except Exception as e:
        return {"error": f"分析失败: {str(e)}"}
