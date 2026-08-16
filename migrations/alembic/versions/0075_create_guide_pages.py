"""create guide hub tables and seed default content

Revision ID: 0075
Revises: 0074
Create Date: 2026-08-10

"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0075'
down_revision: Union[str, None] = '0074'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if not _table_exists('guide_hub_settings'):
        op.create_table(
            'guide_hub_settings',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('kicker', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('title', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('title_highlight', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('description', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('meta_title', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('meta_description', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not _table_exists('guide_categories'):
        op.create_table(
            'guide_categories',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('slug', sa.String(100), unique=True, nullable=False),
            sa.Column('title', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('description', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('icon', sa.String(50), nullable=False, server_default='openwrt'),
            sa.Column('footer_html', postgresql.JSONB(), nullable=True),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    if not _table_exists('guide_pages'):
        op.create_table(
            'guide_pages',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('slug', sa.String(200), unique=True, nullable=False),
            sa.Column('category_id', sa.Integer(), sa.ForeignKey('guide_categories.id', ondelete='SET NULL'), nullable=True),
            sa.Column('title', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('link_label', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('content', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('meta_title', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('meta_description', postgresql.JSONB(), nullable=False, server_default='{}'),
            sa.Column('is_featured', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index('ix_guide_pages_category_id', 'guide_pages', ['category_id'])

    bind = op.get_bind()
    hub_exists = bind.execute(sa.text('SELECT 1 FROM guide_hub_settings WHERE id = 1')).scalar()
    if not hub_exists:
        bind.execute(
            sa.text(
                """
                INSERT INTO guide_hub_settings (id, kicker, title, title_highlight, description, meta_title, meta_description)
                VALUES (
                    1,
                    CAST(:kicker AS jsonb),
                    CAST(:title AS jsonb),
                    CAST(:title_highlight AS jsonb),
                    CAST(:description AS jsonb),
                    CAST(:meta_title AS jsonb),
                    CAST(:meta_description AS jsonb)
                )
                """
            ),
            {
                'kicker': json.dumps({'ru': 'База знаний'}),
                'title': json.dumps({'ru': 'Центр'}),
                'title_highlight': json.dumps({'ru': 'инструкций'}),
                'description': json.dumps(
                    {'ru': 'Подробные гайды по настройке VPN, прошивке роутеров OpenWRT и установке VLESS Reality.'}
                ),
                'meta_title': json.dumps({'ru': 'Инструкции по VPN и прошивке роутеров OpenWrt | VPN BRO'}),
                'meta_description': json.dumps(
                    {
                        'ru': 'Центр инструкций VPN BRO: прошивка OpenWrt, установка Forkop и Podkop, Zapret, настройка VLESS Reality на ТВ и ПК.'
                    }
                ),
            },
        )

    if bind.execute(sa.text('SELECT COUNT(*) FROM guide_categories')).scalar() == 0:
        bind.execute(
            sa.text(
                """
                INSERT INTO guide_categories (slug, title, description, icon, footer_html, sort_order, is_active)
                VALUES
                (
                    'routers',
                    '{"ru": "Прошивка роутеров"}'::jsonb,
                    '{"ru": "Как выбрать модель, прошить OpenWrt без риска и настроить VPN на роутере."}'::jsonb,
                    'routers',
                    '{"ru": "Не хотите рисковать прошивкой сами? <a href=\\"/routers\\" class=\\"font-semibold text-accent-400 hover:text-accent-300\\">Закажите роутер уже прошитый и настроенный</a> в нашем каталоге."}'::jsonb,
                    0,
                    true
                ),
                (
                    'vpn',
                    '{"ru": "Настройка VPN"}'::jsonb,
                    '{"ru": "Подключение VLESS Reality на телефонах, ТВ, ПК и роутерах."}'::jsonb,
                    'vpn',
                    NULL,
                    1,
                    true
                ),
                (
                    'openwrt',
                    '{"ru": "OpenWRT"}'::jsonb,
                    '{"ru": "Гайды по установке, настройке и обходу блокировок на OpenWRT."}'::jsonb,
                    'openwrt',
                    NULL,
                    2,
                    true
                )
                """
            )
        )

        bind.execute(
            sa.text(
                """
                INSERT INTO guide_pages (slug, category_id, title, link_label, meta_title, meta_description, is_featured, sort_order, is_active)
                SELECT v.slug, c.id, v.title::jsonb, v.link_label::jsonb, v.meta_title::jsonb, v.meta_description::jsonb, v.is_featured, v.sort_order, true
                FROM (VALUES
                    ('openwrt-routers', 'routers', '{"ru":"Как прошить роутер на OpenWrt — полный гайд"}', '{"ru":"Как прошить роутер на OpenWrt — полный гайд"}', '{"ru":"Прошивка роутера на OpenWrt — как выбрать модель | VPN BRO"}', '{"ru":"Как выбрать роутер под OpenWrt и прошить без риска «кирпича»: требования, модели и пошаговый гайд VPN BRO."}', true, 0),
                    ('netis', 'routers', '{"ru":"Прошивка Netis N6 OpenWRT"}', '{"ru":"Прошивка Netis N6 OpenWRT"}', '{"ru":"Прошивка Netis N6 на OpenWrt — инструкция | VPN BRO"}', '{"ru":"Пошаговая прошивка Netis N6 на OpenWrt: подготовка, установка, восстановление и настройка VPN на роутере."}', false, 1),
                    ('xiaomi-ax3000t-xmir', 'routers', '{"ru":"Xiaomi AX3000 OpenWRT"}', '{"ru":"Xiaomi AX3000 OpenWRT"}', '{"ru":"Прошивка Xiaomi AX3000T на OpenWrt — инструкция | VPN BRO"}', '{"ru":"Прошивка Xiaomi AX3000T на OpenWrt через xmir-patcher: безопасная установка и подготовка к VPN."}', false, 2),
                    ('keenetic', 'routers', '{"ru":"Keenetic: HydraRoute Neo"}', '{"ru":"Keenetic: HydraRoute Neo"}', '{"ru":"Keenetic и HydraRoute Neo — установка и настройка | VPN BRO"}', '{"ru":"Установка и настройка HydraRoute Neo на Keenetic: VPN на весь дом без клиентских приложений на каждом устройстве."}', false, 3),
                    ('happ-android-tv', 'vpn', '{"ru":"Настройка Happ на Android TV / Apple TV"}', '{"ru":"Настройка Happ на Android TV / Apple TV"}', '{"ru":"Настройка Happ на Android TV и Apple TV | VPN BRO"}', '{"ru":"Настройка Happ на Android TV и Apple TV: подключение VLESS Reality и запуск VPN на телевизоре за несколько минут."}', false, 0),
                    ('forkop', 'openwrt', '{"ru":"Установка Forkop"}', '{"ru":"Установка Forkop"}', '{"ru":"Установка Forkop на OpenWrt — Podkop Plus форк | VPN BRO"}', '{"ru":"Установка Forkop (бывший Podkop Plus) на OpenWrt: подписки, XHTTP, Zapret/ByeDPI и расширенная маршрутизация."}', false, 0),
                    ('podkop', 'openwrt', '{"ru":"Установка Podkop"}', '{"ru":"Установка Podkop"}', '{"ru":"Установка Podkop на OpenWrt — точечный VPN | VPN BRO"}', '{"ru":"Установка Podkop на OpenWrt одной командой: точечная маршрутизация YouTube, Telegram и заблокированных сайтов через VLESS."}', false, 1),
                    ('zapret', 'openwrt', '{"ru":"Установка Zapret"}', '{"ru":"Установка Zapret"}', '{"ru":"Установка Zapret на OpenWrt — анти-DPI | VPN BRO"}', '{"ru":"Установка Zapret на OpenWrt: обход DPI, настройка антиблокировок и стабильный доступ к заблокированным ресурсам."}', false, 2),
                    ('youtube-8k', 'openwrt', '{"ru":"YouTube Unblock + Zapret"}', '{"ru":"YouTube Unblock + Zapret"}', '{"ru":"YouTube Unblock + Zapret на OpenWrt | VPN BRO"}', '{"ru":"YouTube Unblock и Zapret на OpenWrt: разблокировка YouTube, стабильный стриминг и настройка вместе с VPN."}', false, 3)
                ) AS v(slug, category_slug, title, link_label, meta_title, meta_description, is_featured, sort_order)
                JOIN guide_categories c ON c.slug = v.category_slug
                """
            )
        )


def downgrade() -> None:
    if _table_exists('guide_pages'):
        op.drop_index('ix_guide_pages_category_id', table_name='guide_pages')
        op.drop_table('guide_pages')
    if _table_exists('guide_categories'):
        op.drop_table('guide_categories')
    if _table_exists('guide_hub_settings'):
        op.drop_table('guide_hub_settings')


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text('SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = :name)'),
        {'name': table_name},
    )
    return result.scalar()
