-- Local development seed: four users in one demo organization.
-- Password for all seeded users: visionqc123
-- Never use these credentials or this pattern in a hosted environment.

-- ---------------------------------------------------------------------------
-- Auth users
-- ---------------------------------------------------------------------------

insert into auth.users (
    instance_id, id, aud, role, email, encrypted_password,
    email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
    created_at, updated_at,
    confirmation_token, email_change, email_change_token_new, recovery_token
)
values
    ('00000000-0000-0000-0000-000000000000',
     '11111111-1111-4111-8111-111111111111', 'authenticated', 'authenticated',
     'owner@visionqc.local', crypt('visionqc123', gen_salt('bf')),
     now(), '{"provider":"email","providers":["email"]}', '{"full_name":"Asha Owner"}',
     now(), now(), '', '', '', ''),
    ('00000000-0000-0000-0000-000000000000',
     '22222222-2222-4222-8222-222222222222', 'authenticated', 'authenticated',
     'admin@visionqc.local', crypt('visionqc123', gen_salt('bf')),
     now(), '{"provider":"email","providers":["email"]}', '{"full_name":"Ravi Admin"}',
     now(), now(), '', '', '', ''),
    ('00000000-0000-0000-0000-000000000000',
     '33333333-3333-4333-8333-333333333333', 'authenticated', 'authenticated',
     'operator@visionqc.local', crypt('visionqc123', gen_salt('bf')),
     now(), '{"provider":"email","providers":["email"]}', '{"full_name":"Maya Operator"}',
     now(), now(), '', '', '', ''),
    ('00000000-0000-0000-0000-000000000000',
     '44444444-4444-4444-8444-444444444444', 'authenticated', 'authenticated',
     'analyst@visionqc.local', crypt('visionqc123', gen_salt('bf')),
     now(), '{"provider":"email","providers":["email"]}', '{"full_name":"Dev Analyst"}',
     now(), now(), '', '', '', '')
on conflict (id) do nothing;

insert into auth.identities (
    id, user_id, provider_id, provider, identity_data,
    last_sign_in_at, created_at, updated_at
)
values
    (gen_random_uuid(), '11111111-1111-4111-8111-111111111111',
     '11111111-1111-4111-8111-111111111111', 'email',
     '{"sub":"11111111-1111-4111-8111-111111111111","email":"owner@visionqc.local"}',
     now(), now(), now()),
    (gen_random_uuid(), '22222222-2222-4222-8222-222222222222',
     '22222222-2222-4222-8222-222222222222', 'email',
     '{"sub":"22222222-2222-4222-8222-222222222222","email":"admin@visionqc.local"}',
     now(), now(), now()),
    (gen_random_uuid(), '33333333-3333-4333-8333-333333333333',
     '33333333-3333-4333-8333-333333333333', 'email',
     '{"sub":"33333333-3333-4333-8333-333333333333","email":"operator@visionqc.local"}',
     now(), now(), now()),
    (gen_random_uuid(), '44444444-4444-4444-8444-444444444444',
     '44444444-4444-4444-8444-444444444444', 'email',
     '{"sub":"44444444-4444-4444-8444-444444444444","email":"analyst@visionqc.local"}',
     now(), now(), now())
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Demo organization
-- ---------------------------------------------------------------------------

insert into public.orgs (id, name, slug, created_by)
values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Demo Works', 'demo-works',
        '11111111-1111-4111-8111-111111111111')
on conflict (id) do nothing;

insert into public.memberships (org_id, user_id, role)
values
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '11111111-1111-4111-8111-111111111111', 'owner'),
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '22222222-2222-4222-8222-222222222222', 'admin'),
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '33333333-3333-4333-8333-333333333333', 'operator'),
    ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', '44444444-4444-4444-8444-444444444444', 'analyst')
on conflict (org_id, user_id) do nothing;

insert into public.sites (id, org_id, name)
values ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Pune Plant 1')
on conflict (id) do nothing;

insert into public.lines (id, org_id, site_id, name)
values ('cccccccc-cccc-4ccc-8ccc-cccccccccccc',
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'Line A')
on conflict (id) do nothing;

insert into public.products (id, org_id, name)
values ('dddddddd-dddd-4ddd-8ddd-dddddddddddd',
        'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Bottle 500ml')
on conflict (id) do nothing;

insert into public.cameras (id, org_id, line_id, product_id, name, kind, address, config)
values
    ('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
     'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
     'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
     'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
     'Line A - Top', 'usb', '0', '{"view": "top"}'),
    ('ffffffff-ffff-4fff-8fff-ffffffffffff',
     'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
     'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
     'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
     'Line A - Side', 'rtsp', 'rtsp://192.168.1.50:554/stream1', '{"view": "side"}')
on conflict (id) do nothing;

insert into public.settings (org_id, product_id, threshold, delta)
values ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'dddddddd-dddd-4ddd-8ddd-dddddddddddd', 0.46, 0.05)
on conflict (org_id, product_id) do nothing;
