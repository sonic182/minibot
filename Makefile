.PHONY: clean check build publish upload_pypi publish-test publish-dry-run

clean:
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov

check:
	poetry check

build: clean
	poetry build
	twine check dist/*

publish: build
	twine upload dist/*

upload_pypi: publish

publish-test: build
	twine upload --repository testpypi dist/*

publish-dry-run: build
	@echo "Dry run: build + twine check only (no upload)"
