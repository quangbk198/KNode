from setuptools import setup, find_packages

setup(
    name="knode",
    version="0.1.0",
    description="Knowledge graph indexer for Android codebases (Java/Kotlin)",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "tree-sitter>=0.21.0",
        "tree-sitter-java>=0.21.0",
        "tree-sitter-kotlin>=0.21.0",
        "mcp>=1.0.0",
        "fastapi>=0.104.0",
        "uvicorn[standard]>=0.24.0",
        "click>=8.1.0",
        "networkx>=3.2.0",
        "rich>=13.7.0",
        "httpx>=0.25.0",
    ],
    entry_points={
        "console_scripts": [
            "knode=knode.cli:cli",
        ],
    },
)
