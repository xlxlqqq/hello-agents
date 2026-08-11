
# 构建知识库
## 1. 构建知识库（需要 API）
python run.py ingest knowledge_docs/ --rebuild

## 2. 构建知识库（mock embedding，无需 API）
python run.py ingest knowledge_docs/ --mock-embedding

# 运行代理
python run.py run

# 2. 查看知识库
python run.py stats

# 3. 全流程审查 + 修复 + 复检

python run.py review samples/input.docx --json

python run.py repair samples/input.docx --json

python run.py full samples/input.docx --skip-llm --mock-embedding

# 4. 仅复检（对比原始 vs 修复后）
python run.py validate samples/input.docx --repaired output/full/input_repaired.docx --skip-llm
 
# 5. PDF 演示（fallback 模式，能看到解析但修复仅批注）
python run.py full path/to/sample.pdf --format pdf --skip-llm --mock-embedding