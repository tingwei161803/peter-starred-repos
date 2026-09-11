"""把 starred repo 分到七個主題分類。

分類靠 GitHub topics 為主、description / repo 名稱關鍵字為輔,依序比對——
第一個命中的規則就是它的分類,所以 RULES 的順序即優先權,規則要由「窄」排到「寬」:
一個同時掛 claude-code 與 llm 的 repo 應該落在「編碼助手」而不是「模型」。

之所以不用 LLM 逐筆分類而用規則:幾百筆要能隨時重跑、結果要可預測可稽核,
規則錯了看得出來是哪一條錯、改一行就好;LLM 分類則每次重跑結果都可能漂移。
"""

import re

# (key, 英文全名, 中文全名, 英文短名, 中文短名)
# 短名是給圖表座標軸用的 —— 中文全名(「編碼助手與開發工具」)放進長條圖一定黏在一起,
# 而靠切標點自動縮短只對部分名稱有效(「RAG、記憶與知識庫」可以,「編碼助手與開發工具」不行),
# 所以直接寫死短名,不要碰運氣。
CATEGORIES = [
    ("agents",   "Agents & Automation",        "Agent 與自動化",     "Agents",   "Agent"),
    ("coding",   "Coding & Dev Tools",         "編碼助手與開發工具", "Coding",   "編碼工具"),
    ("rag",      "RAG, Memory & Knowledge",    "RAG、記憶與知識庫",  "RAG",      "RAG"),
    ("llm",      "Models, Training & Serving", "模型、訓練與推論",   "Models",   "模型"),
    ("media",    "Speech, Vision & Media",     "語音、影像與多模態", "Media",    "多模態"),
    ("learning", "Learning & Awesome Lists",   "學習資源與精選清單", "Learning", "學習資源"),
    ("apps",     "Apps & Everything Else",     "應用與其他",         "Apps",     "其他"),
]

FALLBACK = "apps"

# (分類 key, topic 關鍵字集合, description/名稱 正則)
RULES = [
    (
        "coding",
        {
            "claude-code", "claude", "codex", "cursor", "copilot", "github-copilot",
            "mcp", "model-context-protocol", "agent-skills", "claude-desktop",
            "developer-tools", "devtools", "cli", "vscode", "ide", "code-review",
            "code-generation", "coding-agent", "aider", "windsurf", "cline",
            "terminal", "neovim", "git", "code-assistant", "subagents", "skills",
            "claude-skills", "agent-sdk", "sdk", "vibe-coding", "spec-driven-development",
        },
        re.compile(
            r"(\bclaude code\b|\bcoding agent\b|\bcode (assistant|review|generation|editor|search)\b|"
            r"\bmcp server\b|\bmodel context protocol\b|\bcli\b|\bide\b|\bsdk\b|\bframework for build|\bbuild .{0,15}apps? in\b|\bvs ?code\b|"
            r"\bpull request\b|\bdeveloper tool\b|\bslash command\b|\bdev environment\b|"
            r"\bagent skills?\b|\bskills? for\b|\bagent sdk\b|\bvibe.?coding\b|"
            r"/(skills?|agent-skills|.*-skill)$|\bcodex skill\b|\bplugins? for\b|"
            r"\bgithub (repo|repository) (to|into)\b|\bsoftware engineer(ing)? agent\b)",
            re.I,
        ),
    ),
    (
        "rag",
        {
            "rag", "retrieval-augmented-generation", "retrieval", "vector-database",
            "vectordb", "embeddings", "knowledge-graph", "graphrag", "memory",
            "semantic-search", "document-parsing", "ocr", "pdf", "chunking",
            "vector-search", "knowledge-base", "long-term-memory", "context-engineering",
            "search-engine", "web-search",
        },
        re.compile(
            r"(\bretrieval[- ]augmented\b|\brag\b|\bvector (database|store|search)\b|"
            r"\bembedding|\bknowledge graph\b|\bsemantic search\b|\bmemory (layer|for)\b|"
            r"\bdocument (parsing|extraction|understanding|conver)|\blong[- ]term memory\b|"
            r"\bparse .{0,20}(pdf|document)|\bpdf.{0,15}(markdown|parse)|\bsearch engine\b|"
            r"\brecall what matters\b|\bcontext engineering\b)",
            re.I,
        ),
    ),
    (
        "agents",
        {
            "ai-agents", "agents", "agent", "agentic-ai", "agentic", "multi-agent",
            "autonomous-agents", "agent-framework", "workflow", "orchestration",
            "browser-use", "computer-use", "web-agent", "agent-based-model",
            "llm-agent", "multi-agent-systems", "crewai", "langgraph", "autogen",
            "automation", "swarm", "deep-research", "n8n", "rpa", "gui-agent",
            "langchain", "llamaindex", "workflow-automation",
        },
        re.compile(
            r"(\bai agent|\bagentic\b|\bmulti[- ]agent\b|\bautonomous agent|\bagent (framework|harness|laborator)|"
            r"\bagent orchestration\b|\bbrowser (automation|agent)\b|\bcomputer use\b|\bgui agent\b|"
            r"\bworkflow automation\b|\bdeep research\b|\bn8n\b|\bagents? (that|for|in)\b|"
            r"\bself[- ]improving agents\b|\bautomated (research|workflow)|\bopenmanus\b|"
            r"\brpa\b|\bdigital (worker|employee)\b|自動化|自动化)",
            re.I,
        ),
    ),
    (
        "media",
        {
            "text-to-speech", "tts", "speech", "asr", "speech-recognition", "whisper",
            "voice", "voice-clone", "audio", "music", "music-generation",
            "text-to-video", "video-generation", "video", "image-generation",
            "diffusion", "stable-diffusion", "computer-vision", "vision",
            "multimodal", "vision-language-model", "vlm", "3d", "avatar",
            "digital-human", "image-segmentation", "ocr-recognition", "transcription",
            "subtitles", "speech-to-text", "text-to-image",
        },
        re.compile(
            r"(\btts\b|\basr\b|\btext[- ]to[- ]speech\b|\bspeech[- ]to[- ]text\b|"
            r"\bspeech recognition\b|\bvoice (clon|ai|agent|interview)|\bvoice\b.{0,20}\bmodel\b|"
            r"\btranscri(be|ption)\b|\bsubtitle|\bwhisper\b|\bpodcast\b|"
            r"\b(video|image|music|audio|3d) (generat|production)|\bvlm\b|\basr\b|\btranscript|\btext[- ]to[- ](video|image|音)\b|"
            r"\bdiffusion model\b|\bvision[- ]language\b|\bmultimodal\b|\bsegment(ation)? (anything|model)\b|"
            r"\bdigital human\b|\bavatar\b|\bportrait|\bshort video\b|\b短视频\b|\b短影|"
            r"語音|语音|配音|影片生成|视频|影音|音視訊)",
            re.I,
        ),
    ),
    (
        "learning",
        {
            "awesome", "awesome-list", "tutorial", "course", "book", "learning",
            "education", "guide", "handbook", "roadmap", "examples", "cookbook",
            "prompt-engineering", "prompts", "papers", "paper", "survey",
            "interview", "cheatsheet", "notes", "curated-list", "resources",
            "system-design", "study", "leetcode", "computer-science", "dataset",
            "benchmark-dataset", "reading-list",
        },
        re.compile(
            r"(\bawesome[- ]|\bcurated (list|collection)\b|\btutorial|\bcourse\b|\bhands[- ]on\b|"
            r"\blearn(ing)? (path|resources?|notes?)\b|\bguide (to|for)\b|\bhandbook\b|\broadmap\b|"
            r"\bcookbook\b|\bcollection of (papers|prompts|resources|examples|notes|full time|jobs)\b|"
            r"\breading list\b|\bprompt(s| engineering| hub)\b|\bstudy notes\b|\bbook notes\b|"
            r"\bpaper(s)? (reading|explained|search|list|notes)\b|\bmust[- ]read\b|"
            r"\bsystem design\b|\bself[- ]learning\b|\bresources for\b|\bbest resources\b|"
            r"\bnotes (for|on)\b|\bexplan(ation|ations) to\b|\blabs? notes\b|\bworkshop\b|"
            r"\bassignments\b|\bcheat ?sheet\b|\bglossary\b|\bdictionary\b|\bwordlist\b|"
            r"(教學|教材|課程|课程|笔记|筆記|指南|论文|論文|自学|自學|研读|研讀|清单|清單|题库|題庫|真经|真經))",
            re.I,
        ),
    ),
    (
        "llm",
        {
            "llm", "llms", "large-language-models", "large-language-model",
            "transformer", "transformers", "fine-tuning", "finetuning", "lora",
            "quantization", "inference", "pretraining", "rlhf", "reinforcement-learning",
            "deep-learning", "machine-learning", "nlp", "pytorch", "model-serving",
            "vllm", "gguf", "llama", "qwen", "mixture-of-experts", "moe",
            "evaluation", "benchmark", "distillation", "reasoning", "chain-of-thought",
            "gpt", "openai", "gemini", "anthropic", "deepseek", "local-llm", "ollama",
            "ai-safety", "alignment", "red-teaming", "jailbreak",
        },
        re.compile(
            r"(\blarge language model|\bllm\b|\bfine[- ]tun|\bpre[- ]train|\bquantiz|"
            r"\binference (engine|server|service|code)\b|\btransformer|\bneural network\b|"
            r"\bdeep learning\b|\bfoundation model\b|\breasoning (model|chain)|\bo1[- ]like\b|\b(gemma|llama|qwen|mistral|phi|deepseek|glm|kimi)\b|"
            r"\breinforcement learning\b|\brlhf\b|\beval(uator|uation|s)\b|\bbenchmark\b|"
            r"\blanguage model|\bdistill|\bjailbreak|\bred[- ]team|\balignment\b|"
            r"\bopen[- ]source (frontier|model)|\bmodel (weights|zoo|serving|hub)\b)",
            re.I,
        ),
    ),
]


# repo 名稱本身就是強訊號的樣式(對「名稱」單獨比對,才能用 $ 錨點)
NAME_RULES = [
    ("coding",   re.compile(r"(^|[-_/])(skills?|agent-skills|plugins?|sdk|cli|mcp)$|"
                            r"[-_](skill|skills|mcp|cli|sdk)$|^(claude|codex|cursor)-", re.I)),
    ("agents",   re.compile(r"(^|[-_/])(agents?|swarm|crew|workflows?)$|[-_]agents?$|"
                            r"^n8n[-_]|[-_]n8n$", re.I)),
    ("learning", re.compile(r"(^|[-_/])(books?|notes?|papers?|resources?|awesome|cookbook|"
                            r"handbook|roadmap|guidelines?)$|[-_](notes?|papers?|resources?|"
                            r"guidelines?|101|labs)$|^awesome[-_]", re.I)),
]


def classify(repo):
    topics = {t.lower() for t in (repo.get("topics") or [])}
    full_name = (repo.get("full_name") or "").lower()
    name = full_name.split("/")[-1]
    desc = repo.get("description") or ""
    # repo 名稱常常就是最好的訊號(例:xxx/agent-skills、xxx/awesome-llm),
    # 所以把名稱一起丟進 haystack;分隔符換空白讓 \b 邊界能對上。
    haystack = desc + " " + full_name + " " + full_name.replace("-", " ").replace("_", " ").replace("/", " ")

    for key, topic_set, pattern in RULES:
        if topics & topic_set or pattern.search(haystack):
            return key
    # topics / description 都沒命中時,再看名稱樣式(這輪比較寬鬆,所以放後面)
    for key, pattern in NAME_RULES:
        if pattern.search(name):
            return key
    return FALLBACK


# ---------------------------------------------------------------------------
# 人工覆寫:規則抓錯、且不值得為了單一個案去動規則的情形。
#
# 為什麼需要這張表:GitHub topics 是作者自己掛的,品質參差。最常見的壞訊號是
# 「agent」這個 topic —— unsloth 和 LlamaFactory 都掛了它,但它們其實是微調工具。
# 與其把規則改到能區分這種個案(改完通常會誤傷別的),不如在這裡明確記一筆。
# 這張表是逐筆讀過每個 repo 的描述之後整理出來的。
# ---------------------------------------------------------------------------
OVERRIDES = {
    # topics 掛了空泛的 agent,實際是訓練/微調工具
    "unslothai/unsloth": "llm",
    "hiyouga/LlamaFactory": "llm",
    "jeinlee1991/chinese-llm-benchmark": "llm",
    "xorbitsai/inference": "llm",
    "ai-dynamo/dynamo": "llm",
    "NVIDIA/personaplex": "llm",
    "Google-Health/medgemma": "llm",
    "SakanaAI/self-adaptive-llms": "llm",
    "LLMSELECTOR/LLMSELECTOR": "llm",
    "ZongqianLi/ReasonGraph": "llm",
    "DanielSun94/conversational_diagnosis": "llm",
    # 教材/清單被誤判成技術類
    "microsoft/generative-ai-for-beginners": "learning",
    "dair-ai/Prompt-Engineering-Guide": "learning",
    "karpathy/LLM101n": "learning",
    "naklecha/llama3-from-scratch": "learning",
    "Infrasys-AI/AISystem": "learning",
    "GAIR-NLP/cognition-engineering": "learning",
    "WTFAcademy/WTF-Langchain": "learning",
    "tomasonjo/blogs": "learning",
    "MIT-LCP/mimic-code": "learning",
    "lucy-cxy/oss-investment-scorecard": "learning",
    "geshan/au-companies-providing-work-visa-sponsorship": "learning",
    "jobright-ai/2026-Account-New-Grad": "learning",
    "jordan-cutler/tech-work-terms": "learning",
    "sadransh/awsome-list-of-cv-and-resume-templetes": "learning",
    "Ernyoke/certified-gcp-cloud-engineer": "learning",
    "https-deeplearning-ai/sc-agent-governance": "learning",
    "kevin801221/amazing-github-repos-everyday": "learning",
    "pipecat-ai/voice-ai-primer-web": "learning",
    "ChatPRD/lennys-podcast-transcripts": "learning",
    "Penny777btc/lenny-podcast-chinese": "learning",
    "LennysNewsletter/lennys-newsletterpodcastdata": "learning",
    "TGoldsack1/Corpora_for_Lay_Summarisation": "learning",
    "HECTA-UoM/PLABA-MU": "learning",
    "attal-kush/PLABA": "learning",
    # 開發工具
    "richards199999/Thinking-Claude": "coding",
    "microsoft/PromptWizard": "coding",
    "firecrawl/open-lovable": "coding",
    "bmad-code-org/BMAD-METHOD": "coding",
    "coderamp-labs/gitingest": "coding",
    "ahmedkhaleel2004/gitdiagram": "coding",
    "nvbn/thefuck": "coding",
    "Cocoon-AI/architecture-diagram-generator": "coding",
    "yandex/perforator": "coding",
    "osanseviero/InstantCoder": "coding",
    "microsoft/data-formulator": "coding",
    "matt-ye/Toutour": "coding",
    "kcchien/model-thinking": "coding",
    "openai/privacy-filter": "coding",
    "certimate-go/certimate": "coding",
    "fleetdm/fleet": "coding",
    "uiverse-io/galaxy": "coding",
    "MobinX/awesome-mcp-list": "coding",
    "MadcowD/ell": "coding",
    # Agent
    "SakanaAI/AI-Scientist": "agents",
    "openclaw/openclaw": "agents",
    "mindverse/Second-Me": "agents",
    "jina-ai/node-DeepResearch": "agents",
    "mshumer/OpenDeepResearcher": "agents",
    "Thytu/Agentarium": "agents",
    "andrewyng/openworker": "agents",
    "google-gemini/gemini-fullstack-langgraph-quickstart": "agents",
    # 語音影像多模態
    "harry0703/MoneyPrinterTurbo": "media",
    "suno-ai/bark": "media",
    "LeeJunHyun/Image_Segmentation": "media",
    "senstella/parakeet-mlx": "media",
    "axcore/tartube": "media",
    "deepbeepmeep/Cosmos1GP": "media",
    "imelnyk/ArxivPapers": "media",
    "HighCWu/flux-4bit": "media",
    "thepersonalaicompany/amurex-backend": "media",
    "ngxson/smolvlm-realtime-webcam": "media",
    "QwenLM/Qwen2-Audio": "media",
    "PKU-YuanGroup/LLaVA-CoT": "media",
    "boson-ai/higgs-audio": "media",
    "Tramac/awesome-semantic-segmentation-pytorch": "media",
    "Winston774/ai-music-channel-starter": "media",
    "Hao0321/video-autopilot-kit": "media",
    # RAG / 知識庫
    "NVIDIA/NeMo-Retriever": "rag",
    "nlweb-ai/NLWeb": "rag",
    "echohive42/AI-reads-books-page-by-page": "rag",
    "SNOWTEAM2023/MedRAG": "rag",
    "chrschy/fact-finder": "rag",
    "shcherbak-ai/contextgem": "rag",
    # 應用
    "ayangweb/Awesome-BongoCat": "apps",
    "heyform/heyform": "apps",
    "DavidHDev/react-bits": "apps",
    "yazelin/ai-chant-magic": "apps",
    "baturyilmaz/wordpecker-app": "apps",
}


_rule_classify = classify


def classify(repo):
    """先看覆寫表,沒有才走規則。"""
    name = repo.get("full_name")
    if name in OVERRIDES:
        return OVERRIDES[name]
    return _rule_classify(repo)
