---
name: get-online-info
description: 互联网信息获取功能，使用阿里云 IQS 服务。
---

# Get Online Information

## 使用方法

1. 设置环境变量 `ALIYUN_API_KEY` 为您的阿里云 API Key

## 示例代码

- linux: python get_online_info.py --data '{"query": "北京天气"}'
- windows: windows: python get_online_info.py --data "{\"query\": \"北京天气\"}"


## **接口定义**

### **函数参数**

query: string | 必填 | 搜索问题。建议30字符以内，超出500字符会被截断。
timeRange: string | 默认: "NoLimit" | 时间范围："OneDay", "OneWeek", "OneMonth", "OneYear", "NoLimit"。
category: string | 默认: None | 查询分类(如finance, law, medical, internet, tax, news_province, news_center)，多个行业用逗号分隔。一般通用场景，不要指定category，会影响召回效果。
engineType: string | 默认: "Generic" | 引擎类型："Generic"(标准版,返回约10条),"GenericAdvanced"(增强版,返回40-80条，收费选项), "LiteAdvanced"(轻量版,返回1-50条)。
city: string | 默认: None | 城市名，如“北京市”。仅对Generic引擎生效。
ip: string | 默认: None | 位置IP。优先级低于城市，仅对Generic引擎生效。
mainText: bool | 默认: False | 是否返回长正文。
markdownText: bool | 默认: False | 是否返回markdown格式正文。
richMainBody: bool | 默认: False | 是否返回富文本全正文。
summary: bool | 默认: False | 是否返回增强摘要(收费选项)。
rerankScore: bool | 默认: True | 是否进行Rerank并返回得分。

### **错误处理**

- 如果环境变量 `ALIYUN_API_KEY` 未设置，会抛出 `ValueError` 异常
- 如果 API 请求失败，会抛出 `RuntimeError` 异常
- 其他异常会按原样抛出