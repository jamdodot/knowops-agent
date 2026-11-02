# KnowOps Agent — 开发入口（薄封装）
#
# 真正的实现在 scripts/kw.py，这里只做转发。
# 原因：Windows 默认没有 make（Git Bash 也不带），但文档里到处写的是 `make up`；
# 两套实现必然漂移，所以只留一套实现 + 两个入口。
# 没有 make 的机器直接跑：python scripts/kw.py <task>

PY ?= python
KW := $(PY) scripts/kw.py

.PHONY: help up down clean logs psql schema seed smoke-db codegen verify

help:      ## 列出所有任务
	@$(KW) help

up:        ## 起 PG 容器（M0 唯一容器）并等待 healthy
	@$(KW) up

down:      ## 停容器，保留数据
	@$(KW) down

clean:     ## 停容器并删数据卷（会删库，仅开发用）
	@$(KW) clean

logs:      ## 跟随 PG 日志
	@$(KW) logs

psql:      ## 进交互式 psql
	@$(KW) psql

schema:    ## 数据卷已存在时补建表（init 脚本只在首次初始化时跑）
	@$(KW) schema

seed:      ## 灌 M0 最小种子数据
	@$(KW) seed

smoke-db:  ## ACL 冒烟：public / 部门祖先链 / 审批例外 / 空数组边界
	@$(KW) smoke-db

codegen:   ## api/openapi.yaml → agent-service/api/schemas.py + java-service/contract
	@$(KW) codegen

verify:    ## 契约 / SQL / 模型三方自检（不需要数据库，可进 CI）
	@$(KW) verify
