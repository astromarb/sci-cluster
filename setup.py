from setuptools import setup, find_packages

setup(
    name="sci-cluster",
    version="0.0.1",
    description="Helper utilities for the sci-cluster project",
    packages=find_packages(exclude=("tests", "docs")),
    include_package_data=True,
    python_requires=">=3.8",
)
