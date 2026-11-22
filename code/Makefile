PYTHON ?= python3
PORT ?= 8000
VENV_PYTHON := .venv/bin/python

.PHONY: setup build migrate run test
.DELETE_ON_ERROR:

setup: .venv/.installed .django-secret

$(VENV_PYTHON):
	@"$(PYTHON)" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "项目依赖需要 Python 3.11 或更高版本，可用 make setup PYTHON=python3.12 选择解释器。")'
	"$(PYTHON)" -m venv .venv

.venv/.installed: requirements.txt | $(VENV_PYTHON)
	$(VENV_PYTHON) -m pip --disable-pip-version-check install -r requirements.txt
	@touch $@

.django-secret: | $(VENV_PYTHON)
	@umask 077 && $(VENV_PYTHON) -c 'import secrets; print(secrets.token_urlsafe(48))' > "$@"

build: setup
	$(VENV_PYTHON) -m compileall -q manage.py qiwu
	$(VENV_PYTHON) manage.py check

migrate: setup
	$(VENV_PYTHON) manage.py migrate --noinput

run: migrate
	$(VENV_PYTHON) manage.py runserver 127.0.0.1:$(PORT)

test: setup
	$(VENV_PYTHON) manage.py test
