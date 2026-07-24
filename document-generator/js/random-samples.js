/**
 * Static Indian-market sample values for one-click random field fill.
 * Each array has exactly 10 entries.
 */
(function (global) {
  const SAMPLES = {
    sellerNames: [
      'Cocoblu Retail India Pvt Ltd',
      'Cloudtail India Pvt Ltd',
      'Appario Retail Private Limited',
      'RetailEZ Commerce Pvt Ltd',
      'Trigur India Private Limited',
      'RK WorldInfocom Pvt Ltd',
      'Darshita Aashiyana Pvt Ltd',
      'Omni Retail India Pvt Ltd',
      'Kreon Finn Financial Services Pvt Ltd',
      'Tech-Connect Retail Pvt Ltd',
    ],

    sellerDispatchAddresses: [
      'Plot No. 12, Sector 18, Udyog Vihar Phase IV, Gurgaon, Haryana 122015',
      'Survey No. 45/2, Bommasandra Industrial Area, Bengaluru, Karnataka 560099',
      'B-204, MIDC, Andheri East, Mumbai, Maharashtra 400093',
      'Godown No. 7, Kundli Industrial Area, Sonipat, Haryana 131028',
      'Plot 88, GIDC Estate, Vatva, Ahmedabad, Gujarat 382445',
      'Door No. 14, SIDCO Industrial Estate, Ambattur, Chennai, Tamil Nadu 600058',
      'E-42, RIICO Industrial Area, Mansarovar, Jaipur, Rajasthan 302020',
      'Unit 3B, Logistic Park, Bhiwandi, Thane, Maharashtra 421302',
      'Warehouse 9, Patparganj Industrial Area, Delhi 110092',
      'Plot 26, Ganga Nagar, Uppal, Hyderabad, Telangana 500039',
    ],

    sellerRegisteredAddresses: [
      '9th Floor, Vaishnavi Corporate Park, Bellandur, Bengaluru, Karnataka 560103',
      'Unit 401, DLF Cyber City, Phase III, Gurgaon, Haryana 122002',
      'A- Wing, 5th Floor, Supreme Business Park, Powai, Mumbai 400076',
      'Tower B, 12th Floor, World Trade Centre, Noida, Uttar Pradesh 201301',
      '2nd Floor, Prestige Shantiniketan, Whitefield, Bengaluru 560048',
      'Office 702, Express Trade Towers, Sector 132, Noida 201304',
      'Level 8, Candor TechSpace, Sector 62, Noida 201309',
      'Plot 15, IT Park, Hinjewadi Phase 2, Pune, Maharashtra 411057',
      '3rd Floor, RMZ Millenia, Adyar, Chennai, Tamil Nadu 600020',
      'Suite 501, Cyber Pearl, HITEC City, Hyderabad, Telangana 500081',
    ],

    stationAddresses: [
      { line1: 'Mathura Road, Near Ashram Chowk', line2: 'New Delhi, Delhi 110014' },
      { line1: 'Hinjewadi Phase 1, Rajiv Gandhi Infotech Park', line2: 'Pune, Maharashtra 411057' },
      { line1: 'Outer Ring Road, Marathahalli', line2: 'Bengaluru, Karnataka 560037' },
      { line1: 'SV Road, Andheri West', line2: 'Mumbai, Maharashtra 400058' },
      { line1: 'NH-48, Near Rajiv Chowk', line2: 'Gurgaon, Haryana 122001' },
      { line1: 'Anna Salai, Teynampet', line2: 'Chennai, Tamil Nadu 600018' },
      { line1: 'Park Street, Near Maidan', line2: 'Kolkata, West Bengal 700016' },
      { line1: 'SG Highway, Bodakdev', line2: 'Ahmedabad, Gujarat 380054' },
      { line1: 'Ameerpet Main Road', line2: 'Hyderabad, Telangana 500016' },
      { line1: 'MG Road, Civil Lines', line2: 'Jaipur, Rajasthan 302006' },
    ],

    postpaidPlans: [
      { name: 'Airtel Postpaid Infinity Family 699 Plan', charges: 699 },
      { name: 'Airtel Postpaid Infinity 549 Plan', charges: 549 },
      { name: 'Airtel Postpaid Infinity 999 Plan', charges: 999 },
      { name: 'Airtel Postpaid Infinity 399 Plan', charges: 399 },
      { name: 'Airtel Postpaid Infinity 449 Plan', charges: 449 },
      { name: 'Airtel Infinity Family 1299 Plan', charges: 1299 },
      { name: 'Airtel Postpaid Infinity 799 Plan', charges: 799 },
      { name: 'Airtel Postpaid Infinity 1199 Plan', charges: 1199 },
      { name: 'Airtel Postpaid Infinity 499 Plan', charges: 499 },
      { name: 'Airtel Black Plan 1799', charges: 1799 },
    ],

    rentProperties: [
      {
        houseNo: 'Flat 402, Tower B',
        propertyAddress: 'Green Park Extension, New Delhi, Delhi 110016',
        landlordName: 'Priya Verma',
      },
      {
        houseNo: 'House No. 18, 2nd Floor',
        propertyAddress: 'Koramangala 5th Block, Bengaluru, Karnataka 560095',
        landlordName: 'Rajesh Kumar Mehta',
      },
      {
        houseNo: 'B-304, Shanti Apartments',
        propertyAddress: 'Andheri East, Mumbai, Maharashtra 400069',
        landlordName: 'Sunita Desai',
      },
      {
        houseNo: 'Plot 12, Lane 4',
        propertyAddress: 'Banjara Hills, Hyderabad, Telangana 500034',
        landlordName: 'Anil Reddy',
      },
      {
        houseNo: 'Flat 7A, Rose Residency',
        propertyAddress: 'Salt Lake Sector V, Kolkata, West Bengal 700091',
        landlordName: 'Debashish Banerjee',
      },
      {
        houseNo: 'Villa 9, Palm Grove',
        propertyAddress: 'Viman Nagar, Pune, Maharashtra 411014',
        landlordName: 'Neha Kulkarni',
      },
      {
        houseNo: 'Flat 201, Sunrise Heights',
        propertyAddress: 'Vaishali Nagar, Jaipur, Rajasthan 302021',
        landlordName: 'Vikram Singh Rathore',
      },
      {
        houseNo: '3rd Floor, 45 Park Street',
        propertyAddress: 'Alwarpet, Chennai, Tamil Nadu 600018',
        landlordName: 'Lakshmi Iyer',
      },
      {
        houseNo: 'Flat 1102, Skyline Towers',
        propertyAddress: 'Sector 62, Noida, Uttar Pradesh 201309',
        landlordName: 'Mohit Agarwal',
      },
      {
        houseNo: 'House 22, Civil Lines',
        propertyAddress: 'Lucknow, Uttar Pradesh 226001',
        landlordName: 'Farhan Siddiqui',
      },
    ],

    landlordNames: [
      'Priya Verma',
      'Rajesh Kumar Mehta',
      'Sunita Desai',
      'Anil Reddy',
      'Debashish Banerjee',
      'Neha Kulkarni',
      'Vikram Singh Rathore',
      'Lakshmi Iyer',
      'Mohit Agarwal',
      'Farhan Siddiqui',
    ],

    houseNumbers: [
      'Flat 402, Tower B',
      'House No. 18, 2nd Floor',
      'B-304, Shanti Apartments',
      'Plot 12, Lane 4',
      'Flat 7A, Rose Residency',
      'Villa 9, Palm Grove',
      'Flat 201, Sunrise Heights',
      '3rd Floor, 45 Park Street',
      'Flat 1102, Skyline Towers',
      'House 22, Civil Lines',
    ],

    propertyAddresses: [
      'Green Park Extension, New Delhi, Delhi 110016',
      'Koramangala 5th Block, Bengaluru, Karnataka 560095',
      'Andheri East, Mumbai, Maharashtra 400069',
      'Banjara Hills, Hyderabad, Telangana 500034',
      'Salt Lake Sector V, Kolkata, West Bengal 700091',
      'Viman Nagar, Pune, Maharashtra 411014',
      'Vaishali Nagar, Jaipur, Rajasthan 302021',
      'Alwarpet, Chennai, Tamil Nadu 600018',
      'Sector 62, Noida, Uttar Pradesh 201309',
      'Civil Lines, Lucknow, Uttar Pradesh 226001',
    ],
  };

  function pick(key) {
    const list = SAMPLES[key];
    if (!list || !list.length) return null;
    return list[Math.floor(Math.random() * list.length)];
  }

  global.NOOBIUS_RANDOM_SAMPLES = {
    data: SAMPLES,
    pick,
  };
}(typeof window !== 'undefined' ? window : globalThis));
