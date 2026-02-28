---
name: get-online-info
description: 互联网信息获取功能，使用阿里云 IQS 服务。
---

# Get Online Information

## 使用方法

1. 设置环境变量 `ALIYUN_API_KEY` 为您的阿里云 API Key

## 示例代码

- linux: curl --location 'https://cloud-iqs.aliyuncs.com/search/unified' --header 'Authorization: Bearer $ALIYUN_API_KEY' --header 'Content-Type: application/json' --data '{query: "北京天气"}'
- windows: curl --location "https://cloud-iqs.aliyuncs.com/search/unified" --header "Authorization: Bearer %ALIYUN_API_KEY%" --header "Content-Type: application/json" --data "{\"query\": \"北京天气\"}"


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

### **返回值**

返回 API 响应的 JSON 数据，包含以下主要字段：

- requestId: 请求RequestId, 排查问题时可以提供此信息
- pageItems: 搜索结果列表，每个项目包含：
  - title: 网站标题
  - link: 网站地址
  - snippet: 网页动态摘要
  - publishedTime: 网页发布时间
  - mainText: 解析得到的网页全正文（当 mainText=True 时）
  - richMainBody: 富文本全正文（当 richMainBody=True 时）
  - markdownText: 解析得到的网页全文markdown格式（当 markdownText=True 时）
  - images: 网页中的图片地址列表
  - hostname: 网站的站点名称
  - summary: 增强摘要（当 summary=True 时）
  - hostAuthorityScore: 动态权威性评分
  - websiteAuthorityScore: 静态权威分
  - correlationTag: 相关性标签
- sceneItems: 垂类场景结果列表

### **错误处理**

- 如果环境变量 `ALIYUN_API_KEY` 未设置，会抛出 `ValueError` 异常
- 如果 API 请求失败，会抛出 `RuntimeError` 异常
- 其他异常会按原样抛出

### **错误码**

404:
  InvalidAccessKeyId.NotFound: Specified access key is not found. | 让用户检查并确保AccessKey/Secret正确。
403:
  Retrieval.NotActivate: Please activate AI search service | 请让用户下单或联系您的客户经理进行开通。
  Retrieval.Arrears: Please recharge first. | 账户金额不足，请让用户充值。
  Retrieval.NotAuthorised: Please authorize the AliyunIQSFullAccess privilege to the sub-account. | 子账号没有进行授权。
  Retrieval.TestUserPeriodExpired: The test period has expired. | 测试已到期(15天有效)，让用户联系客户经理转正式。
429:
  Retrieval.Throttling.User: Request was denied due to user flow control. | 超出限流规格，让用户联系客户经理进行升配。
  Retrieval.TestUserQueryPerDayExceeded: The query per day exceed the limit. | 测试超出日限额(1000次/天)，让用户联系客户经理转正式。