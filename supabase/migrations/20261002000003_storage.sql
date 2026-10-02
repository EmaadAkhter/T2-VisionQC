-- Private evidence bucket. Object path convention:
--   {org_id}/{camera_id}/{inspection_uid}.png
-- Access is checked against the first path segment (org id).

insert into storage.buckets (id, name, public)
values ('evidence', 'evidence', false)
on conflict (id) do nothing;

create policy evidence_select_member on storage.objects
    for select using (
        bucket_id = 'evidence'
        and public.is_org_member(((storage.foldername(name))[1])::uuid)
    );

create policy evidence_insert_operator on storage.objects
    for insert with check (
        bucket_id = 'evidence'
        and public.has_org_role(
            ((storage.foldername(name))[1])::uuid,
            array['owner', 'admin', 'quality_manager', 'operator', 'technician']
        )
    );

create policy evidence_update_operator on storage.objects
    for update using (
        bucket_id = 'evidence'
        and public.has_org_role(
            ((storage.foldername(name))[1])::uuid,
            array['owner', 'admin', 'quality_manager', 'operator', 'technician']
        )
    );

create policy evidence_delete_admin on storage.objects
    for delete using (
        bucket_id = 'evidence'
        and public.has_org_role(
            ((storage.foldername(name))[1])::uuid,
            array['owner', 'admin']
        )
    );
