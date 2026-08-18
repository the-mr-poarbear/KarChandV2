export const formatPrice = (price: number) => {
    const rounded = Math.floor(price / 1000) * 1000;

    return new Intl.NumberFormat("en-US").format(rounded);
};