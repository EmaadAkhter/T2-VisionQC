-- VisionQC row-level security policies.
-- UI hiding is not security: every tenant table is protected here, deny by
-- default. Helper functions are security definer so policies can consult
-- memberships without recursive RLS.

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

create or replace function public.is_org_member(target_org uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1 from public.memberships
        where org_id = target_org and user_id = auth.uid()
    );
$$;

create or replace function public.org_role(target_org uuid)
returns text
language sql
stable
security definer
set search_path = public
as $$
    select role from public.memberships
    where org_id = target_org and user_id = auth.uid()
    limit 1;
$$;

create or replace function public.has_org_role(target_org uuid, roles text[])
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select coalesce(public.org_role(target_org) = any(roles), false);
$$;

-- ---------------------------------------------------------------------------
-- Enable RLS everywhere
-- ---------------------------------------------------------------------------

alter table public.orgs enable row level security;
alter table public.profiles enable row level security;
alter table public.memberships enable row level security;
alter table public.invitations enable row level security;
alter table public.sites enable row level security;
alter table public.lines enable row level security;
alter table public.products enable row level security;
alter table public.cameras enable row level security;
alter table public.models enable row level security;
alter table public.settings enable row level security;
alter table public.inspections enable row level security;
alter table public.audit_log enable row level security;

-- ---------------------------------------------------------------------------
-- orgs
-- ---------------------------------------------------------------------------

create policy orgs_select_member on public.orgs
    for select using (public.is_org_member(id));

create policy orgs_update_admin on public.orgs
    for update using (public.has_org_role(id, array['owner', 'admin']))
    with check (public.has_org_role(id, array['owner', 'admin']));

create policy orgs_delete_owner on public.orgs
    for delete using (public.has_org_role(id, array['owner']));

-- No direct insert policy: organizations are created through create_org().

-- ---------------------------------------------------------------------------
-- profiles
-- ---------------------------------------------------------------------------

create policy profiles_select_self_or_colleague on public.profiles
    for select using (
        id = auth.uid()
        or exists (
            select 1
            from public.memberships mine
            join public.memberships theirs on theirs.org_id = mine.org_id
            where mine.user_id = auth.uid() and theirs.user_id = profiles.id
        )
    );

create policy profiles_update_self on public.profiles
    for update using (id = auth.uid())
    with check (id = auth.uid());

-- ---------------------------------------------------------------------------
-- memberships
-- ---------------------------------------------------------------------------

create policy memberships_select_member on public.memberships
    for select using (public.is_org_member(org_id));

create policy memberships_insert_admin on public.memberships
    for insert with check (public.has_org_role(org_id, array['owner', 'admin']));

create policy memberships_update_admin on public.memberships
    for update using (public.has_org_role(org_id, array['owner', 'admin']))
    with check (public.has_org_role(org_id, array['owner', 'admin']));

create policy memberships_delete_admin on public.memberships
    for delete using (public.has_org_role(org_id, array['owner', 'admin']));

-- ---------------------------------------------------------------------------
-- invitations (acceptance happens through accept_invitation(), not here)
-- ---------------------------------------------------------------------------

create policy invitations_select_admin on public.invitations
    for select using (public.has_org_role(org_id, array['owner', 'admin']));

create policy invitations_insert_admin on public.invitations
    for insert with check (
        public.has_org_role(org_id, array['owner', 'admin'])
        and invited_by = auth.uid()
    );

create policy invitations_delete_admin on public.invitations
    for delete using (public.has_org_role(org_id, array['owner', 'admin']));

-- ---------------------------------------------------------------------------
-- Factory hierarchy: sites / lines / cameras
-- ---------------------------------------------------------------------------

create policy sites_select_member on public.sites
    for select using (public.is_org_member(org_id));

create policy sites_write_admin on public.sites
    for all using (public.has_org_role(org_id, array['owner', 'admin', 'technician']))
    with check (public.has_org_role(org_id, array['owner', 'admin', 'technician']));

create policy lines_select_member on public.lines
    for select using (public.is_org_member(org_id));

create policy lines_write_admin on public.lines
    for all using (public.has_org_role(org_id, array['owner', 'admin', 'technician']))
    with check (public.has_org_role(org_id, array['owner', 'admin', 'technician']));

create policy cameras_select_member on public.cameras
    for select using (public.is_org_member(org_id));

create policy cameras_write_admin on public.cameras
    for all using (public.has_org_role(org_id, array['owner', 'admin', 'technician']))
    with check (public.has_org_role(org_id, array['owner', 'admin', 'technician']));

-- ---------------------------------------------------------------------------
-- Products / models / settings (quality policy)
-- ---------------------------------------------------------------------------

create policy products_select_member on public.products
    for select using (public.is_org_member(org_id));

create policy products_write_quality on public.products
    for all using (public.has_org_role(org_id, array['owner', 'admin', 'quality_manager']))
    with check (public.has_org_role(org_id, array['owner', 'admin', 'quality_manager']));

create policy models_select_member on public.models
    for select using (public.is_org_member(org_id));

create policy models_write_quality on public.models
    for all using (public.has_org_role(org_id, array['owner', 'admin', 'quality_manager']))
    with check (public.has_org_role(org_id, array['owner', 'admin', 'quality_manager']));

create policy settings_select_member on public.settings
    for select using (public.is_org_member(org_id));

create policy settings_write_quality on public.settings
    for all using (public.has_org_role(org_id, array['owner', 'admin', 'quality_manager']))
    with check (public.has_org_role(org_id, array['owner', 'admin', 'quality_manager']));

-- ---------------------------------------------------------------------------
-- inspections
-- ---------------------------------------------------------------------------

create policy inspections_select_member on public.inspections
    for select using (public.is_org_member(org_id));

create policy inspections_insert_operator on public.inspections
    for insert with check (
        public.has_org_role(
            org_id,
            array['owner', 'admin', 'quality_manager', 'operator', 'technician']
        )
    );

create policy inspections_update_operator on public.inspections
    for update using (
        public.has_org_role(
            org_id,
            array['owner', 'admin', 'quality_manager', 'operator', 'technician']
        )
    )
    with check (
        public.has_org_role(
            org_id,
            array['owner', 'admin', 'quality_manager', 'operator', 'technician']
        )
    );

create policy inspections_delete_admin on public.inspections
    for delete using (public.has_org_role(org_id, array['owner', 'admin']));

-- ---------------------------------------------------------------------------
-- audit log
-- ---------------------------------------------------------------------------

create policy audit_select_admin on public.audit_log
    for select using (public.has_org_role(org_id, array['owner', 'admin']));

create policy audit_insert_member on public.audit_log
    for insert with check (
        public.is_org_member(org_id) and actor = auth.uid()
    );
