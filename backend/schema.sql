-- 申万行业资金流看板 · 建表脚本（MySQL 8.0 / utf8mb4 / InnoDB）
-- 幂等：可重复执行

CREATE DATABASE IF NOT EXISTS stock_data
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE stock_data;

-- 交易日历
CREATE TABLE IF NOT EXISTS t_trade_cal (
  cal_date      DATE        NOT NULL,
  exchange      VARCHAR(8)  NOT NULL,
  is_open       TINYINT     NOT NULL DEFAULT 1,
  pretrade_date DATE        NULL,
  PRIMARY KEY (cal_date, exchange)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 行业分类（一/二/三级统一，sw_code 去 .SI 后缀）
CREATE TABLE IF NOT EXISTS t_industry (
  id           BIGINT UNSIGNED AUTO_INCREMENT,
  sw_code      VARCHAR(16) NOT NULL,
  name         VARCHAR(64) NOT NULL,
  level        TINYINT     NOT NULL,
  parent_code  VARCHAR(16) NULL,
  industry_code VARCHAR(16) NULL,
  l1_name      VARCHAR(64) NULL,
  is_pub       CHAR(1)     NULL,
  updated_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_sw_code (sw_code),
  KEY idx_parent (parent_code),
  KEY idx_level (level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 成分股（时点，index_code 去 .SI 后缀）
CREATE TABLE IF NOT EXISTS t_industry_member (
  id         BIGINT UNSIGNED AUTO_INCREMENT,
  index_code VARCHAR(16) NOT NULL,
  con_code   VARCHAR(16) NOT NULL,
  in_date    DATE        NULL,
  out_date   DATE        NULL,
  is_new     CHAR(1)     NULL,
  updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_member (index_code, con_code, in_date),
  KEY idx_con (con_code),
  KEY idx_out (out_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 股票基础信息
CREATE TABLE IF NOT EXISTS t_stock_basic (
  ts_code    VARCHAR(16) NOT NULL,
  symbol     VARCHAR(8)  NULL,
  name       VARCHAR(64) NULL,
  area       VARCHAR(32) NULL,
  industry   VARCHAR(64) NULL,
  market     VARCHAR(16) NULL,
  list_date  DATE        NULL,
  updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (ts_code),
  KEY idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 个股每日资金流（原始单位：万元 / 手）
CREATE TABLE IF NOT EXISTS t_moneyflow (
  ts_code         VARCHAR(16) NOT NULL,
  trade_date      DATE        NOT NULL,
  buy_sm_vol      DECIMAL(20,2) NULL,
  buy_sm_amount   DECIMAL(20,2) NULL,
  sell_sm_vol     DECIMAL(20,2) NULL,
  sell_sm_amount  DECIMAL(20,2) NULL,
  buy_md_vol      DECIMAL(20,2) NULL,
  buy_md_amount   DECIMAL(20,2) NULL,
  sell_md_vol     DECIMAL(20,2) NULL,
  sell_md_amount  DECIMAL(20,2) NULL,
  buy_lg_vol      DECIMAL(20,2) NULL,
  buy_lg_amount   DECIMAL(20,2) NULL,
  sell_lg_vol     DECIMAL(20,2) NULL,
  sell_lg_amount  DECIMAL(20,2) NULL,
  buy_elg_vol     DECIMAL(20,2) NULL,
  buy_elg_amount  DECIMAL(20,2) NULL,
  sell_elg_vol    DECIMAL(20,2) NULL,
  sell_elg_amount DECIMAL(20,2) NULL,
  net_mf_vol      DECIMAL(20,2) NULL,
  net_mf_amount   DECIMAL(20,2) NULL,
  updated_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (ts_code, trade_date),
  KEY idx_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 行业资金流聚合（亿元）
CREATE TABLE IF NOT EXISTS t_industry_flow (
  sw_code      VARCHAR(16) NOT NULL,
  level        TINYINT     NOT NULL,
  trade_date   DATE        NOT NULL,
  sm           DECIMAL(20,4) NULL,
  md           DECIMAL(20,4) NULL,
  lg           DECIMAL(20,4) NULL,
  elg          DECIMAL(20,4) NULL,
  main         DECIMAL(20,4) NULL,
  net          DECIMAL(20,4) NULL,
  turnover     DECIMAL(20,4) NULL,
  member_count INT         NULL,
  updated_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (sw_code, trade_date),
  KEY idx_date (trade_date),
  KEY idx_level_date (level, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 采集审计日志
CREATE TABLE IF NOT EXISTS t_fetch_log (
  id           BIGINT UNSIGNED AUTO_INCREMENT,
  task_name    VARCHAR(64) NOT NULL,
  status       VARCHAR(16) NOT NULL,
  start_at     DATETIME    NULL,
  end_at       DATETIME    NULL,
  rows_fetched INT         NULL,
  rows_written INT         NULL,
  error_msg    TEXT        NULL,
  created_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_task (task_name, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
