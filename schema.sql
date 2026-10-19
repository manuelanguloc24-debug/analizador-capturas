-- Ejecutar UNA sola vez en Supabase > SQL Editor.
create extension if not exists pgcrypto;

create table if not exists public.capturas (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  fecha_actividad date not null,
  departamento text not null,
  archivo text not null,
  hash_sha256 char(64) not null,
  hora_ocr text,
  hora_final text,
  hora_meta text not null default '06:00',
  diferencia_min integer,
  cumplimiento text not null default 'Pendiente',
  estado text not null default 'revision',
  observaciones text not null default '',
  imagen_path text,
  ocr_selected jsonb,
  ocr_size jsonb,
  imagen_expira_en timestamptz,
  constraint hora_final_formato check (hora_final is null or hora_final ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$')
);

create index if not exists capturas_busqueda
  on public.capturas (fecha_actividad desc, departamento);
create index if not exists capturas_hash
  on public.capturas (fecha_actividad, hash_sha256);
create index if not exists capturas_retencion
  on public.capturas (imagen_expira_en)
  where imagen_path is not null;

alter table public.capturas enable row level security;
revoke all on public.capturas from anon, authenticated;
grant all on public.capturas to service_role;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('capturas-ocr', 'capturas-ocr', false, 15728640,
        array['image/jpeg','image/png','image/webp'])
on conflict (id) do update set public = false;