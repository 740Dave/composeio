from setuptools import setup, find_packages

setup(
    name="mkdocs-smd",
    version="0.1.0",
    description="MkDocs plugin for rendering SMD (Semantic Markdown) files as hyperlinked documentation",
    author="David Lee Burnett",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "mkdocs>=1.5",
        "mkdocs-material>=9.0",
        "pymdown-extensions>=10.0",
        "pyyaml>=6.0",
    ],
    entry_points={
        "mkdocs.plugins": [
            "mkdocs-smd = mkdocs_smd:SmdPlugin",
        ],
    },
)
