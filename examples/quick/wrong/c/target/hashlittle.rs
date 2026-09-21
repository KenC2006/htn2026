#[inline]
fn rot(x: u32, k: u32) -> u32 {
    x.rotate_left(k)
}

#[inline]
fn mix(a: &mut u32, b: &mut u32, c: &mut u32) {
    *a = a.wrapping_sub(*c);
    *a ^= rot(*c, 4);
    *c = c.wrapping_add(*b);

    *b = b.wrapping_sub(*a);
    *b ^= rot(*a, 6);
    *a = a.wrapping_add(*c);

    *c = c.wrapping_sub(*b);
    *c ^= rot(*b, 8);
    *b = b.wrapping_add(*a);

    *a = a.wrapping_sub(*c);
    *a ^= rot(*c, 16);
    *c = c.wrapping_add(*b);

    *b = b.wrapping_sub(*a);
    *b ^= rot(*a, 19);
    *a = a.wrapping_add(*c);

    *c = c.wrapping_sub(*b);
    *c ^= rot(*b, 4);
    *b = b.wrapping_add(*a);
}

#[inline]
fn final_mix(a: &mut u32, b: &mut u32, c: &mut u32) {
    *c ^= *b;
    *c = c.wrapping_sub(rot(*b, 14));

    *a ^= *c;
    *a = a.wrapping_sub(rot(*c, 11));

    *b ^= *a;
    *b = b.wrapping_sub(rot(*a, 25));

    *c ^= *b;
    *c = c.wrapping_sub(rot(*b, 16));

    *a ^= *c;
    *a = a.wrapping_sub(rot(*c, 4));

    *b ^= *a;
    *b = b.wrapping_sub(rot(*a, 14));

    *c ^= *b;
    *c = c.wrapping_sub(rot(*b, 24));
}

pub fn hashlittle(key: Vec<u8>, initval: u32) -> u32 {
    let length: u32 = key.len() as u32;

    let init: u32 = 0xdeadbeefu32.wrapping_add(length).wrapping_add(initval);
    let mut a: u32 = init;
    let mut b: u32 = init;
    let mut c: u32 = init;

    let mut pos: usize = 0;
    let mut remaining: u32 = length;

    while remaining > 12 {
        let w0 = (key[pos] as u32)
            | ((key[pos + 1] as u32) << 8)
            | ((key[pos + 2] as u32) << 16)
            | ((key[pos + 3] as u32) << 24);
        let w1 = (key[pos + 4] as u32)
            | ((key[pos + 5] as u32) << 8)
            | ((key[pos + 6] as u32) << 16)
            | ((key[pos + 7] as u32) << 24);
        let w2 = (key[pos + 8] as u32)
            | ((key[pos + 9] as u32) << 8)
            | ((key[pos + 10] as u32) << 16)
            | ((key[pos + 11] as u32) << 24);

        a = a.wrapping_add(w0);
        b = b.wrapping_add(w1);
        c = c.wrapping_add(w2);

        mix(&mut a, &mut b, &mut c);

        pos += 12;
        remaining -= 12;
    }

    if remaining == 0 {
        return c;
    }

    let k = &key[pos..pos + remaining as usize];

    if remaining >= 1 {
        a = a.wrapping_add(k[0]);
    }
    if remaining >= 2 {
        a = a.wrapping_add((k[1] as u32) << 8);
    }
    if remaining >= 3 {
        a = a.wrapping_add((k[2] as u32) << 16);
    }
    if remaining >= 4 {
        a = a.wrapping_add((k[3] as u32) << 24);
    }
    if remaining >= 5 {
        b = b.wrapping_add(k[4] as u32);
    }
    if remaining >= 6 {
        b = b.wrapping_add((k[5] as u32) << 8);
    }
    if remaining >= 7 {
        b = b.wrapping_add((k[6] as u32) << 16);
    }
    if remaining >= 8 {
        b = b.wrapping_add((k[7] as u32) << 24);
    }
    if remaining >= 9 {
        c = c.wrapping_add(k[8] as u32);
    }
    if remaining >= 10 {
        c = c.wrapping_add((k[9] as u32) << 8);
    }
    if remaining >= 11 {
        c = c.wrapping_add((k[10] as u32) << 16);
    }
    if remaining >= 12 {
        c = c.wrapping_add((k[11] as u32) << 24);
    }

    final_mix(&mut a, &mut b, &mut c);

    c
}
