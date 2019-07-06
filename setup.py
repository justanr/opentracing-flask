from setuptools import find_packages, setup

if __name__ == "__main__":
    setup(
        name="opentracing-flask",
        version="0.0.1",
        author="Alec Nikolas Reiter",
        license="MIT",
        description="Adds jaeger tracing to flask",
        include_package_data=True,
        packages=find_packages("src"),
        package_dir={"": "src"},
        install_requires=["flask>=1.0", "opentracing>=2.0,<3", "blinker"],
    )
